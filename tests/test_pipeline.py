"""End-to-end: train -> evaluate -> deploy -> explain on a synthetic warehouse."""

import duckdb

from uplift_engine import pipeline
from uplift_engine.warehouse import connect


def test_full_pipeline(cfg):
    with connect(cfg) as con:
        board = pipeline.train(cfg, con, log=lambda _: None)
        assert board.is_champion.sum() == 1
        assert any(m.startswith("ensemble:") for m in board.model)
        champion = board.loc[board.is_champion, "model"].item()
        assert not board.loc[board.is_champion, "is_baseline"].item()

        results = pipeline.evaluate(cfg, con, log=lambda _: None)
        assert set(results.model) == {champion, "response", "random"}
        pipeline.evaluate(cfg, con, log=lambda _: None)
        assert con.execute("SELECT count(*) FROM meta.test_access_log").fetchone()[0] == 2

        scores = pipeline.deploy(cfg, con, log=lambda _: None)
        assert len(scores) == 4000
        assert set(scores.action) <= {"send", "holdout", "skip"}
        assert (scores.loc[scores.action != "skip", "expected_profit"] > 0).all()
        assert scores["rank"].is_unique

        importance = pipeline.explain(cfg, con, n_rows=2000, log=lambda _: None)
        assert importance.share.sum() > 0.99
        assert "express_points_spent" in importance.feature.head(3).tolist()

    con = duckdb.connect(str(cfg.paths.warehouse), read_only=True)
    tables = {
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='ml'"
        ).fetchall()
    }
    assert {
        "leaderboard",
        "qini_curves",
        "test_results",
        "scores",
        "policy",
        "drift",
        "balance",
        "feature_importance",
        "profit_curves",
        "runs",
    } <= tables
    assert (cfg.paths.artifacts_dir / "model_prod.joblib").exists()


def test_api_scores_with_deployed_model(cfg, monkeypatch):
    from fastapi.testclient import TestClient

    from uplift_engine import api

    with connect(cfg) as con:
        pipeline.train(cfg, con, log=lambda _: None)
        pipeline.deploy(cfg, con, log=lambda _: None)
    monkeypatch.setenv("UTE_MODEL", str(cfg.paths.artifacts_dir / "model_prod.joblib"))
    api.bundle.cache_clear()
    client = TestClient(api.app)

    assert client.get("/health").json() == {"status": "ok"}
    info = client.get("/model").json()
    assert info["break_even_uplift"] > 0

    rows = [
        {"client_id": "a", "age": 30, "gender": "F", "express_points_spent": 20.0},
        {"age": 75, "gender": "M", "express_points_spent": 0.0},
    ]
    body = client.post("/score", json={"clients": rows}).json()
    assert [s["client_id"] for s in body["scores"]] == ["a", None]
    assert body["scores"][0]["expected_uplift"] >= body["scores"][1]["expected_uplift"]

    assert client.post("/score", json={"clients": [{"bogus": 1}]}).status_code == 422
    assert client.post("/score", json={"clients": []}).status_code == 422
    api.bundle.cache_clear()
