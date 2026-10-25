"""R4: stale or partly rebuilt features are refused. R5: no historical snapshot is required."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import yaml

from conftest import REPO, write_fixture_config
from raw_fixture import make_historical_snapshot, make_raw
from uplift_engine import cli, lineage, pipeline, release
from uplift_engine.config import load_config
from uplift_engine.lineage import LineageMismatchError

pytestmark = pytest.mark.integration


def _transformed(tmp: Path, monkeypatch, snapshot: bool = True) -> tuple:
    files = make_raw(tmp / "raw")
    if snapshot:
        make_historical_snapshot(tmp / "snapshot", tmp / "snapshot_manifest.json")
    config = write_fixture_config(tmp, files)
    monkeypatch.setenv("UTE_ROOT", str(REPO))
    cli.ingest(config=config)
    cli.transform(config=config)
    return load_config(config, root=REPO), config


def _deploy(cfg):
    with duckdb.connect(str(cfg.paths.warehouse)) as con:
        return pipeline.deploy(cfg, con, log=lambda _: None)


def test_fresh_build_is_accepted(tmp_path, monkeypatch):
    cfg, _ = _transformed(tmp_path, monkeypatch)
    with duckdb.connect(str(cfg.paths.warehouse)) as con:
        assert lineage.require_fresh_features(con, cfg)["status"] == "succeeded"


def test_changed_cutoff_is_refused(tmp_path, monkeypatch):
    _, config = _transformed(tmp_path, monkeypatch)
    raw = yaml.safe_load(config.read_text())
    raw["dataset"]["as_of"] = "2019-03-01 00:00:00"
    config.write_text(yaml.safe_dump(raw))
    with pytest.raises(LineageMismatchError, match="built for as_of"):
        _deploy(load_config(config, root=REPO))


def test_changed_feature_sql_is_refused(tmp_path, monkeypatch):
    cfg, _ = _transformed(tmp_path, monkeypatch)
    real = lineage.feature_code_fingerprint
    monkeypatch.setattr(
        lineage, "feature_code_fingerprint", lambda root, as_of: real(root, as_of) + "-edited"
    )
    with pytest.raises(LineageMismatchError, match="feature SQL"):
        _deploy(cfg)


def test_reingested_raw_data_is_refused(tmp_path, monkeypatch):
    _, config = _transformed(tmp_path, monkeypatch)
    files = make_raw(tmp_path / "raw", seed=99)  # different raw bytes
    raw = yaml.safe_load(config.read_text())
    raw["dataset"]["files"] = files
    config.write_text(yaml.safe_dump(raw))
    cli.ingest(config=config)
    with pytest.raises(LineageMismatchError, match="re-ingested"):
        _deploy(load_config(config, root=REPO))


def test_transform_failing_partway_invalidates_the_workspace(tmp_path, monkeypatch):
    import subprocess

    cfg, config = _transformed(tmp_path, monkeypatch)
    files = make_raw(tmp_path / "raw", variant="contradictory")
    raw = yaml.safe_load(config.read_text())
    raw["dataset"]["files"] = files
    config.write_text(yaml.safe_dump(raw))
    cli.ingest(config=config)
    with pytest.raises(subprocess.CalledProcessError):
        cli.transform(config=config)  # dbt fails after it may have replaced some tables
    cfg = load_config(config, root=REPO)
    with duckdb.connect(str(cfg.paths.warehouse)) as con:
        status = con.execute(
            "SELECT status FROM meta.feature_build ORDER BY built_at DESC LIMIT 1"
        ).fetchone()[0]
    assert status == "failed"
    with pytest.raises(LineageMismatchError, match="is failed"):
        _deploy(cfg)


def test_release_without_historical_snapshot(tmp_path, monkeypatch):
    """A fresh clone has no local evidence snapshot: the release must still be complete."""
    files = make_raw(tmp_path / "raw")
    config = write_fixture_config(tmp_path, files)  # snapshot paths point at nothing
    monkeypatch.setenv("UTE_ROOT", str(REPO))
    monkeypatch.setenv("UTE_CONFIG", str(config))
    monkeypatch.delenv("UTE_MARTS_DIR", raising=False)
    cfg = load_config(config, root=REPO)
    ok, reason = pipeline.historical_available(cfg)
    assert not ok and "not found" in reason

    cli.run(config=config)
    rid, rdir = release.active_release(cfg)
    manifest = json.loads((rdir / "manifest.json").read_text())
    assert "historical" not in manifest["runs"] and manifest["historical_available"] is False
    res = json.loads((cfg.paths.marts_dir / "results.json").read_text())
    assert res["historical_test"] == {"available": False, "reason": reason}
    assert not (rdir / "marts" / "mart_historical_test.parquet").exists()

    from fastapi.testclient import TestClient

    from uplift_engine import api

    api._load.cache_clear()
    assert TestClient(api.app).get("/ready").json()["release_id"] == rid

    from streamlit.testing.v1 import AppTest

    sys.path.insert(0, str(REPO / "dashboard"))
    for page in sorted((REPO / "dashboard" / "views").glob("*.py")):
        at = AppTest.from_file(str(page), default_timeout=120).run()
        assert not at.exception, (page.name, [e.value for e in at.exception])
        if page.name == "overview.py":
            assert any("unavailable" in w.value for w in at.warning)

    import subprocess

    site = tmp_path / "site" / "index.html"
    subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "build_site.py"),
            "--marts",
            str(rdir / "marts"),
            "--out",
            str(site),
        ],
        check=True,
        cwd=REPO,
    )
    html = site.read_text()
    assert "Unavailable for this release" in html and 'id="hist-qini"' not in html
    assert pd.read_parquet(rdir / "scores.parquet").producer_run_id.nunique() == 1


def test_explicit_historical_command_fails_clearly_without_snapshot(tmp_path, monkeypatch):
    import typer

    files = make_raw(tmp_path / "raw")
    config = write_fixture_config(tmp_path, files)
    monkeypatch.setenv("UTE_ROOT", str(REPO))
    with pytest.raises(typer.Exit):
        cli.historical(config=config)
    make_historical_snapshot(tmp_path / "snapshot", tmp_path / "snapshot_manifest.json")
    assert pipeline.historical_available(load_config(config, root=REPO))[0]
