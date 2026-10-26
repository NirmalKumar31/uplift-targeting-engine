"""Release activation, fault injection and lineage checks on real fixture releases."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from conftest import REPO, write_fixture_config
from raw_fixture import make_historical_snapshot, make_raw
from uplift_engine import cli, release
from uplift_engine.config import load_config

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    tmp = tmp_path_factory.mktemp("rel")
    files = make_raw(tmp / "raw")
    make_historical_snapshot(tmp / "snapshot", tmp / "snapshot_manifest.json")
    config = write_fixture_config(tmp, files)
    mp.setenv("UTE_ROOT", str(REPO))
    mp.setenv("UTE_CONFIG", str(config))
    mp.delenv("UTE_MARTS_DIR", raising=False)
    cli.run(config=config)  # release A, active
    yield load_config(config, root=REPO), config
    mp.undo()


def consumers(cfg) -> dict:
    """Which release the API, dashboard, results and export are serving."""
    from fastapi.testclient import TestClient

    from uplift_engine import api

    api._load.cache_clear()
    client = TestClient(api.app)
    ready = client.get("/ready").json()
    sys.path.insert(0, str(REPO / "dashboard"))
    import common  # dashboard data resolver

    marts, dash_rid = common.source()
    res = json.loads((marts / "results.json").read_text())
    export = json.loads((cfg.paths.marts_dir / "RELEASE.json").read_text())["release_id"]
    return {
        "api": ready["release_id"],
        "api_policy": ready["policy_version"],
        "dashboard": dash_rid,
        "results": res["release_id"],
        "results_policy": res["policy_version"],
        "export": export,
    }


def stage_new_release(config) -> str:
    """Build a complete release without activating it."""
    cfg = load_config(config, root=REPO)
    with duckdb.connect(str(cfg.paths.warehouse)) as con:
        rid = release.new_release(cfg, con)
    for stage in (cli.assess, cli.deploy, cli.historical, cli.explain, cli.marts):
        stage(config=config)
    return rid


def _active(cfg) -> str:
    return release.active_release(cfg)[0]


def test_success_leaves_every_consumer_on_one_release(project):
    cfg, _ = project
    c = consumers(cfg)
    assert c["api"] == c["dashboard"] == c["results"] == c["export"] == _active(cfg)
    assert c["api_policy"] == c["results_policy"]
    camp = pd.read_parquet(release.active_release(cfg)[1] / "campaign_assignment.parquet")
    assert set(camp.policy_version) == {c["api_policy"]}


@pytest.mark.parametrize("boundary", ["RELEASE.json", "manifest.json", "ACTIVE"])
def test_fault_before_or_at_commit_keeps_old_release_everywhere(project, monkeypatch, boundary):
    cfg, config = project
    old = _active(cfg)
    new = stage_new_release(config)
    real = release._atomic_write

    def failing(path, text):
        if Path(path).name == boundary:
            raise OSError(f"injected failure writing {boundary}")
        return real(path, text)

    monkeypatch.setattr(release, "_atomic_write", failing)
    with duckdb.connect(str(cfg.paths.warehouse)) as con, pytest.raises(OSError):
        release.activate(cfg, con, new)
    monkeypatch.undo()
    c = consumers(cfg)
    assert c["api"] == c["dashboard"] == c["results"] == c["export"] == old


def test_fault_after_commit_moves_every_consumer_and_reconcile_repairs_export(project, monkeypatch):
    cfg, config = project
    new = stage_new_release(config)

    def failing_sync(*args, **kwargs):
        raise OSError("injected export failure")

    monkeypatch.setattr(release, "sync_export", failing_sync)
    with duckdb.connect(str(cfg.paths.warehouse)) as con, pytest.warns(UserWarning):
        release.activate(cfg, con, new)
    c = consumers(cfg)
    assert c["api"] == c["dashboard"] == c["results"] == new  # committed: one release
    assert c["export"] != new  # derived copy lags...
    monkeypatch.undo()
    with duckdb.connect(str(cfg.paths.warehouse)) as con:
        release.reconcile(cfg, con)  # ...until reconciled
        statuses = dict(con.execute("SELECT release_id, status FROM meta.releases").fetchall())
    assert consumers(cfg)["export"] == new
    assert statuses[new] == "active" and list(statuses.values()).count("active") == 1


def test_interrupted_export_swap_is_repaired(project):
    cfg, _ = project
    marts, prev = cfg.paths.marts_dir, cfg.paths.marts_dir.with_name("marts.prev")
    marts.rename(prev)  # crash between the two renames
    sys.path.insert(0, str(REPO / "dashboard"))
    import common

    assert common.source()[1] == _active(cfg)  # pointer-resolved: still served meanwhile
    release.reconcile(cfg)
    assert consumers(cfg)["export"] == _active(cfg)


def test_rerunning_deploy_invalidates_downstream_until_rebuilt(project):
    cfg, config = project
    new = stage_new_release(config)
    cli.deploy(config=config)  # a new producer after marts/results were exported
    with pytest.raises(release.ReleaseError) as exc:
        cli.release_cmd(config=config)
    assert "not the latest deploy" in str(exc.value) or "marts were built from" in str(exc.value)
    with duckdb.connect(str(cfg.paths.warehouse)) as con:  # a failed release cannot be reused
        con.execute("UPDATE meta.releases SET status = 'building' WHERE release_id = ?", [new])
    cli.explain(config=config)
    cli.marts(config=config)
    cli.release_cmd(config=config)
    c = consumers(cfg)
    assert c["api"] == c["dashboard"] == c["results"] == c["export"] == new
    assert c["api_policy"] == c["results_policy"]


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("source_fp", "'another-code-generation'", "different generations (source_fp"),
        ("env_fp", "'another-environment'", "different generations (env_fp"),
        ("env_fp", "NULL", "missing env_fp"),
        ("config_hash", "NULL", "missing config_hash"),
    ],
)
def test_mixed_or_missing_lineage_is_rejected(project, column, value, message):
    cfg, config = project
    new = stage_new_release(config)
    with duckdb.connect(str(cfg.paths.warehouse)) as con:
        con.execute(
            f"UPDATE meta.runs SET {column} = {value} WHERE release_id = ? AND stage = 'explain'",
            [new],
        )
        with pytest.raises(release.ReleaseError, match=message.replace("(", r"\(")):
            release.validate(cfg, con, new)


def test_artifact_from_another_producer_is_rejected(project):
    cfg, config = project
    old_dir = release.active_release(cfg)[1]
    new = stage_new_release(config)
    new_dir = release.release_dir(cfg, new)
    (new_dir / "scores.parquet").write_bytes((old_dir / "scores.parquet").read_bytes())
    with (
        duckdb.connect(str(cfg.paths.warehouse)) as con,
        pytest.raises(release.ReleaseError, match=r"scores\.parquet was produced by"),
    ):
        release.validate(cfg, con, new)


def test_tampered_active_file_fails_verification(project):
    from uplift_engine.lineage import LineageMismatchError

    cfg, _ = project
    rdir = release.active_release(cfg)[1]
    path = rdir / "results.json"
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    try:
        with pytest.raises(LineageMismatchError):
            release.verify_file(rdir, "results.json")
    finally:
        path.write_bytes(original)
