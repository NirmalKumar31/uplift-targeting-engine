import json

import duckdb
import pytest

from conftest import REPO, write_fixture_config
from uplift_engine import release
from uplift_engine.config import load_config
from uplift_engine.lineage import ensure_meta


@pytest.fixture
def setup(tmp_path):
    cfg = load_config(write_fixture_config(tmp_path, {}), root=REPO)
    con = duckdb.connect(str(cfg.paths.warehouse))
    ensure_meta(con)
    return cfg, con


def _fake_complete(cfg, con, rid, config_hash="c1"):
    rdir = release.release_dir(cfg, rid)
    for f in release.REQUIRED_FILES:
        (rdir / f).write_text(f"{rid}:{f}")
    (rdir / "marts").mkdir()
    (rdir / "marts" / "mart_x.parquet").write_text(rid)
    for stage in release.REQUIRED_STAGES:
        con.execute(
            "INSERT INTO meta.runs (run_id, stage, release_id, started_at, status, "
            "config_hash, dataset_fp, feature_build_id) VALUES (?, ?, ?, now(), "
            "'succeeded', ?, 'd', 'f')",
            [f"{stage}-{rid}", stage, rid, config_hash],
        )


def test_complete_release_activates_and_swaps_marts(setup):
    cfg, con = setup
    rid = release.new_release(cfg, con)
    _fake_complete(cfg, con, rid)
    release.activate(cfg, con, rid)
    assert release.active_release(cfg)[0] == rid
    assert json.loads((cfg.paths.marts_dir / "RELEASE.json").read_text())["release_id"] == rid
    assert release.verify_file(release.release_dir(cfg, rid), "results.json")


def test_incomplete_release_never_becomes_active(setup):
    cfg, con = setup
    good = release.new_release(cfg, con)
    _fake_complete(cfg, con, good)
    release.activate(cfg, con, good)

    con.execute("UPDATE meta.releases SET created_at = created_at - INTERVAL 1 DAY")
    bad = "rel-bad"
    release.release_dir(cfg, bad).mkdir(parents=True)
    con.execute("INSERT INTO meta.releases VALUES (?, now(), 'building', NULL, NULL)", [bad])
    con.execute(
        "INSERT INTO meta.runs (run_id, stage, release_id, started_at, status) "
        "VALUES ('assess-bad', 'assess', ?, now(), 'failed')",
        [bad],
    )
    with pytest.raises(release.ReleaseError, match="incomplete"):
        release.activate(cfg, con, bad)
    assert release.active_release(cfg)[0] == good
    assert json.loads((cfg.paths.marts_dir / "RELEASE.json").read_text())["release_id"] == good
    assert (
        con.execute("SELECT status FROM meta.releases WHERE release_id = 'rel-bad'").fetchone()[0]
        == "failed"
    )


def test_mismatched_runs_rejected(setup):
    cfg, con = setup
    rid = release.new_release(cfg, con)
    _fake_complete(cfg, con, rid)
    con.execute("UPDATE meta.runs SET config_hash = 'other' WHERE stage = 'explain'")
    with pytest.raises(release.ReleaseError, match="config_hash"):
        release.activate(cfg, con, rid)


def test_tampered_file_fails_verification(setup):
    cfg, con = setup
    rid = release.new_release(cfg, con)
    _fake_complete(cfg, con, rid)
    release.activate(cfg, con, rid)
    (release.release_dir(cfg, rid) / "bundle.joblib").write_text("tampered")
    from uplift_engine.lineage import LineageMismatchError

    with pytest.raises(LineageMismatchError):
        release.verify_file(release.release_dir(cfg, rid), "bundle.joblib")


def test_interrupted_swap_is_repaired(setup):
    cfg, _ = setup
    prev = cfg.paths.marts_dir.with_name("marts.prev")
    prev.mkdir(parents=True)
    (prev / "RELEASE.json").write_text("{}")
    release.restore_marts(cfg)
    assert (cfg.paths.marts_dir / "RELEASE.json").exists()
