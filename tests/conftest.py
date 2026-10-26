from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]


def synthetic_experiment(n: int = 4000, seed: int = 0) -> pd.DataFrame:
    """Uplift rises with express_points_spent and is negative for clients over 70."""
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


def write_fixture_config(tmp: Path, files: dict[str, str], **overrides) -> Path:
    """Small, fast config with all paths under `tmp`."""
    raw = yaml.safe_load((REPO / "configs" / "pipeline.yaml").read_text())
    raw["paths"] = {
        "raw_dir": str(tmp / "raw"),
        "warehouse": str(tmp / "wh.duckdb"),
        "releases_dir": str(tmp / "releases"),
        "marts_dir": str(tmp / "marts"),
        "historical_snapshot": str(tmp / "snapshot"),
        "historical_manifest": str(tmp / "snapshot_manifest.json"),
    }
    raw["dataset"]["files"] = files
    raw["dataset"]["base_url"] = "file:///nonexistent"  # fixtures never download
    raw["experiment"].update(n_folds=2, outer_folds=2, n_bootstrap=30, noise_rankings=20)
    raw["models"]["lightgbm"].update(n_estimators=25, min_child_samples=20)
    raw["models"]["ensemble_top_k"] = 2
    for key, value in overrides.items():
        section, field = key.split("__")
        raw[section][field] = value
    path = tmp / "pipeline.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path
