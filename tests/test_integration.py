"""End-to-end through the real entry points on a miniature raw dataset (no network).

ingest -> transform (dbt build incl. source tests) -> assess -> deploy -> historical -> explain
-> marts -> release, then API, dashboard and static-site smoke tests against the active release,
and the dbt build failures the point-in-time and grain guards must produce.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from conftest import REPO, write_fixture_config
from raw_fixture import SPECIAL, make_historical_snapshot, make_raw
from uplift_engine import cli
from uplift_engine.config import load_config

pytestmark = pytest.mark.integration


def _project(tmp: Path, variant: str | None = None) -> Path:
    files = make_raw(tmp / "raw", variant)
    make_historical_snapshot(tmp / "snapshot", tmp / "snapshot_manifest.json")
    return write_fixture_config(tmp, files)


@pytest.fixture(scope="module")
def built(tmp_path_factory, monkeypatch_module):
    tmp = tmp_path_factory.mktemp("e2e")
    config = _project(tmp)
    monkeypatch_module.setenv("UTE_ROOT", str(REPO))
    cli.run(config=config, skip_ingest=False)
    return load_config(config, root=REPO), config


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def _q(cfg, sql):
    with duckdb.connect(str(cfg.paths.warehouse), read_only=True) as con:
        return con.execute(sql).df()


def test_release_is_active_and_complete(built):
    cfg, _ = built
    from uplift_engine.release import active_release

    rid, rdir = active_release(cfg)
    manifest = json.loads((rdir / "manifest.json").read_text())
    assert set(manifest["runs"]) == {"assess", "deploy", "historical", "explain", "marts"}
    assert (cfg.paths.marts_dir / "RELEASE.json").exists()
    res = json.loads((cfg.paths.marts_dir / "results.json").read_text())
    assert res["release_id"] == rid and res["assessment"]["policies"]
    runs = _q(cfg, "SELECT * FROM meta.runs")
    assert runs.git_commit.notna().all() and runs.source_fp.notna().all()
    assert set(runs.status) == {"succeeded"}


def test_point_in_time_features_and_cleaning(built):
    cfg, _ = built
    f = _q(cfg, "SELECT * FROM features.client_features").set_index("client_id")
    assert f.loc[SPECIAL["teen"], "age"] == 16
    assert pd.isna(f.loc[SPECIAL["bad_age"], "age"])
    assert not f.loc[SPECIAL["late_redeem"], "has_redeemed"]
    early = f.loc[SPECIAL["early_redeem"]]
    assert pd.isna(early.days_issue_to_redeem) and early.redeem_date_invalid
    assert (f.days_issue_to_redeem.dropna() >= 0).all()
    q = _q(cfg, "SELECT * FROM staging.stg_data_quality").set_index("check_name").n_rows
    assert q.filter(like="after as_of (set").iloc[0] == 1
    assert q.filter(like="before card issue").iloc[0] == 1


def test_truthful_age_bands(built):
    cfg, _ = built
    dim = pd.read_parquet(cfg.paths.marts_dir / "dim_client.parquet").set_index("client_id")
    assert dim.loc[SPECIAL["teen"], "age_band"] == "14-17"
    assert dim.loc[SPECIAL["bad_age"], "age_band"] == "Unknown"


def test_history_is_preserved_across_releases(built):
    cfg, config = built
    before = _q(cfg, "SELECT count(DISTINCT run_id) AS n FROM ml.policy").n.iloc[0]
    cli.deploy(config=config)  # a later run appends; earlier evaluations stay queryable
    after = _q(cfg, "SELECT count(DISTINCT run_id) AS n FROM ml.policy").n.iloc[0]
    assert after == before + 1


def test_assessment_never_fits_on_validation_customers_or_labels(built, monkeypatch):
    """Spy on every outer-fold fit; then perturb fold-0 validation labels with folds held fixed
    and require fold 0's fitted procedure to be bit-identical (positive control: fold 1 moves)."""
    import numpy as np

    from uplift_engine import pipeline

    cfg, _ = built
    real_fit, real_load = pipeline.fit_procedure, pipeline.load_experiment
    recorded, calls = [], []

    class Recording(pipeline.StratifiedKFold):
        def split(self, X, y=None, groups=None):
            recorded[:] = list(super().split(X, y, groups))
            return iter(recorded)

    def spy(X, t, y, cfg_, margin, log=print):
        proc = real_fit(X, t, y, cfg_, margin, log)
        calls.append({"ids": set(X.index), "proc": proc})
        return proc

    monkeypatch.setattr(pipeline, "fit_procedure", spy)
    monkeypatch.setattr(pipeline, "StratifiedKFold", Recording)
    with duckdb.connect(str(cfg.paths.warehouse)) as con:
        pipeline.assess(cfg, con, log=lambda _: None)
        experiment = real_load(con)
        X = pipeline.load_features(con, experiment.index)
    ids = experiment.index.to_numpy()
    assert len(calls) == cfg.experiment.outer_folds
    for k, (_, va) in enumerate(recorded):
        assert not calls[k]["ids"] & set(ids[va]), f"fold {k} trained on validation customers"
    first = [c["proc"] for c in calls]

    val0 = set(ids[recorded[0][1]])
    fixed = list(recorded)

    class Fixed:
        def __init__(self, *args, **kwargs):
            pass

        def split(self, X, y=None, groups=None):
            return iter(fixed)

    def flipped(con):
        exp = real_load(con).copy()
        mask = exp.index.isin(val0)
        exp.loc[mask, "target"] = 1 - exp.loc[mask, "target"]
        return exp

    calls.clear()
    monkeypatch.setattr(pipeline, "StratifiedKFold", Fixed)
    monkeypatch.setattr(pipeline, "load_experiment", flipped)
    with duckdb.connect(str(cfg.paths.warehouse)) as con:
        pipeline.assess(cfg, con, log=lambda _: None)
    second = [c["proc"] for c in calls]

    Xv = X.iloc[fixed[0][1]]
    a, b = first[0], second[0]
    assert a.champion == b.champion
    pd.testing.assert_frame_equal(a.calibrator.bins_, b.calibrator.bins_)
    np.testing.assert_array_equal(a.learner.predict(Xv), b.learner.predict(Xv))
    assert a.response_share == b.response_share
    # positive control: fold 1 trains on the flipped customers, so its fit must change
    assert not np.array_equal(first[1].oof[first[1].champion], second[1].oof[second[1].champion])


def test_api_contract_parity_and_campaign(built, monkeypatch):
    from fastapi.testclient import TestClient

    from uplift_engine import api
    from uplift_engine.release import active_release

    cfg, config = built
    monkeypatch.chdir(REPO)
    monkeypatch.setattr(api, "load_config", lambda: cfg)
    api._load.cache_clear()
    client = TestClient(api.app)
    assert client.get("/health").json() == {"status": "alive"}
    ready = client.get("/ready").json()
    rid, rdir = active_release(cfg)
    assert ready["release_id"] == rid

    feats = _q(cfg, "SELECT * FROM features.client_features LIMIT 5")
    records = json.loads(feats.to_json(orient="records"))
    body = client.post("/score", json={"clients": records}).json()
    batch = pd.read_parquet(rdir / "scores.parquet").set_index("client_id")
    for row in body["scores"]:
        assert row["uplift_score"] == pytest.approx(
            batch.loc[row["client_id"], "uplift_score"], abs=1e-9
        )
        assert row["economically_eligible"] == bool(
            batch.loc[row["client_id"], "economically_eligible"]
        )
    assert "not campaign authorization" in body["note"]

    for bad in (
        {},
        {"gender": "X"},
        records[0] | {"age": -999},
        records[0] | {"spend_total": "Infinity"},
    ):
        assert client.post("/score", json={"clients": [bad]}).status_code == 422
    assert client.post("/score", json={"clients": []}).status_code == 422

    camp = pd.read_parquet(rdir / "campaign_assignment.parquet")
    holdout = camp[camp.action == "holdout"].client_id.iloc[0]
    send = camp[camp.action == "send"].client_id.iloc[0]
    cid = camp.campaign_id.iloc[0]
    dec = client.post(
        f"/campaigns/{cid}/authorize", json={"client_ids": [holdout, send, "nobody"]}
    ).json()["decisions"]
    assert [d["authorized"] for d in dec] == [False, True, False]
    assert client.post("/campaigns/other/authorize", json={"client_ids": [send]}).status_code == 404

    out = Path(cfg.paths.releases_dir) / "send.csv"
    cli.export_send_list(out, config=config)
    exported = set(pd.read_csv(out).client_id)
    assert holdout not in exported and send in exported
    assert exported == set(camp[camp.action == "send"].client_id)


def test_api_reports_not_ready_without_a_release(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from uplift_engine import api

    cfg = load_config(write_fixture_config(tmp_path, {}), root=REPO)
    monkeypatch.setattr(api, "load_config", lambda: cfg)
    api._load.cache_clear()
    client = TestClient(api.app)
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503
    assert client.post("/score", json={"clients": [{"n_txn": 1}]}).status_code == 503


def test_failed_stage_leaves_previous_release_active(built):
    cfg, config = built
    from uplift_engine import release
    from uplift_engine.release import active_release

    before = active_release(cfg)[0]
    with duckdb.connect(str(cfg.paths.warehouse)) as con:
        release.new_release(cfg, con)
    cli.historical(config=config)  # only one stage: the release is incomplete
    with pytest.raises(release.ReleaseError):
        cli.release_cmd(config=config)
    assert active_release(cfg)[0] == before


@pytest.mark.parametrize(
    "variant", ["future_transaction", "contradictory", "unmatched_product", "null_transaction_ts"]
)
def test_dbt_build_fails_on_guarded_raw_defects(tmp_path, variant, monkeypatch):
    monkeypatch.setenv("UTE_ROOT", str(REPO))
    config = _project(tmp_path, variant)
    cli.ingest(config=config)
    with pytest.raises(subprocess.CalledProcessError):
        cli.transform(config=config)


def test_static_site_and_dashboard_smoke(built, tmp_path, monkeypatch):
    cfg, _ = built
    site = tmp_path / "site"
    subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "build_site.py"),
            "--marts",
            str(cfg.paths.marts_dir),
            "--out",
            str(site / "index.html"),
        ],
        check=True,
        cwd=REPO,
    )
    html = (site / "index.html").read_text()
    from plotly.offline import get_plotlyjs_version

    assert html.count("cdn.plot.ly") == 1  # plotly.js loaded exactly once...
    assert f"plotly-{get_plotlyjs_version()}.min.js" in html  # ...matching the serializer

    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("UTE_MARTS_DIR", str(cfg.paths.marts_dir))
    monkeypatch.syspath_prepend(str(REPO / "dashboard"))  # app.py's directory, as when served
    for page in sorted((REPO / "dashboard" / "views").glob("*.py")):
        at = AppTest.from_file(str(page), default_timeout=120).run()
        assert not at.exception, (page.name, [e.value for e in at.exception])


def _app(page: str, marts: Path):
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(REPO / "dashboard" / "views" / f"{page}.py"), default_timeout=120)


def test_dashboard_interactions(built, monkeypatch):
    cfg, _ = built
    monkeypatch.setenv("UTE_MARTS_DIR", str(cfg.paths.marts_dir))
    monkeypatch.syspath_prepend(str(REPO / "dashboard"))

    at = _app("clients", cfg.paths.marts_dir).run()
    for text in ("[", "(", ".", "c00000"):
        at.text_input[0].set_value(text).run()
        assert not at.exception, text
    assert at.metric[0].value == "0" or text == "c00000"
    at.text_input[0].set_value(".").run()
    assert at.metric[0].value == "0"  # '.' is literal, not "any character"

    at = _app("simulator", cfg.paths.marts_dir).run()
    before = [m.value for m in at.metric]
    at.slider[1].set_value(300.0).run()
    after = [m.value for m in at.metric]
    assert not at.exception and before != after
    table = at.dataframe[0].value
    current = table[table["Break-even uplift"].str.contains("current")].iloc[0]
    assert current["Break-even uplift"].startswith("1.00%")  # 3 / 300

    from common import send_rows

    dim = pd.read_parquet(cfg.paths.marts_dir / "dim_client.parquet")
    assert set(send_rows(dim).action) == {"send"}
    assert not send_rows(dim).client_id.isin(dim[dim.action == "holdout"].client_id).any()


def test_client_lookup_labelled_unlabelled_unknown(built, monkeypatch):
    from fastapi.testclient import TestClient

    from uplift_engine import api
    from uplift_engine.release import active_release

    cfg, _ = built
    monkeypatch.setattr(api, "load_config", lambda: cfg)
    api._load.cache_clear()
    client = TestClient(api.app)
    scores = pd.read_parquet(active_release(cfg)[1] / "scores.parquet")
    labelled = scores[scores.in_experiment].client_id.iloc[0]
    unlabelled = scores[~scores.in_experiment].client_id.iloc[0]
    assert pd.isna(scores.set_index("client_id").loc[unlabelled, "oof_decile"])

    r = client.get(f"/clients/{labelled}")
    assert r.status_code == 200 and isinstance(r.json()["oof_decile"], int)
    r = client.get(f"/clients/{unlabelled}")
    assert r.status_code == 200 and r.json()["oof_decile"] is None
    assert client.get("/clients/not-a-client").status_code == 404


def test_jsonable_handles_every_missing_marker():
    import numpy as np

    from uplift_engine.api import jsonable

    for missing in (None, np.nan, pd.NA, pd.NaT, float("inf"), np.float64("nan")):
        assert jsonable(missing) is None
    assert jsonable(np.int64(3)) == 3 and isinstance(jsonable(np.int64(3)), int)
    assert jsonable(np.bool_(True)) is True
    assert jsonable(pd.Timestamp("2019-03-19")) == "2019-03-19T00:00:00"
    assert jsonable("send") == "send"
