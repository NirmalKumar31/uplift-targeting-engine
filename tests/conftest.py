"""Synthetic fixtures: a randomised experiment with a known, heterogeneous treatment effect."""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest
import yaml

from uplift_engine.config import load_config


def synthetic_experiment(n: int = 4000, seed: int = 0) -> pd.DataFrame:
    """Uplift rises with `express_points_spent` and is negative for the oldest clients."""
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(
        {
            "client_id": [f"c{i:06d}" for i in range(n)],
            "age": rng.integers(18, 80, n).astype(float),
            "gender": rng.choice(["F", "M", "U"], n),
            "has_redeemed": rng.random(n) < 0.9,
            "n_txn": rng.poisson(10, n).astype(float) + 1,
            "express_points_spent": rng.exponential(5, n),
            "spend_total": rng.gamma(2, 2000, n),
        }
    )
    df.loc[rng.random(n) < 0.05, "age"] = np.nan
    tau = 0.25 * (df.express_points_spent > 5) - 0.10 * (df.age.fillna(40) > 70)
    base = 0.3 + 0.2 * (df.n_txn > 10)
    t = rng.integers(0, 2, n)
    y = (rng.random(n) < np.clip(base + t * tau, 0, 1)).astype(int)
    return df.assign(treatment=t, target=y, true_uplift=tau)


@pytest.fixture(scope="session")
def experiment() -> pd.DataFrame:
    return synthetic_experiment()


@pytest.fixture
def cfg(tmp_path: Path):
    """A fast config pointing at a temporary warehouse seeded with synthetic dbt outputs."""
    raw = yaml.safe_load((Path(__file__).parents[1] / "configs" / "pipeline.yaml").read_text())
    raw["paths"] = {
        "raw_dir": "raw",
        "warehouse": "wh.duckdb",
        "artifacts_dir": "artifacts",
        "marts_dir": "marts",
    }
    raw["experiment"].update(n_folds=2, n_bootstrap=20)
    raw["models"]["lightgbm"].update(n_estimators=30, min_child_samples=20)
    raw["models"]["candidates"] = [
        "random",
        "response",
        "s_learner",
        "t_learner",
        "class_transformation",
    ]
    raw["models"]["ensemble_top_k"] = 2
    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump(raw))
    config = load_config(path, root=tmp_path)

    exp = synthetic_experiment(3000, seed=1)
    unlabelled = synthetic_experiment(1000, seed=2).assign(
        client_id=lambda d: "u" + d.client_id.str[1:]
    )
    con = duckdb.connect(str(config.paths.warehouse))
    features = pd.concat([exp, unlabelled]).drop(columns=["treatment", "target", "true_uplift"])
    for schema in ("features", "staging"):
        con.execute(f"CREATE SCHEMA {schema}")
    con.register("f", features)
    con.execute("CREATE TABLE features.client_features AS SELECT * FROM f")
    con.register("e", exp[["client_id", "treatment", "target"]])
    con.execute("CREATE TABLE staging.stg_experiment AS SELECT * FROM e")
    con.execute("CREATE TABLE staging.stg_transactions AS SELECT 400.0 AS purchase_sum")
    con.close()
    return config
