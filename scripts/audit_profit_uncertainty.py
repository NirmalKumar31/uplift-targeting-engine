"""Audit-only reuse of the existing test for fixed-policy uncertainty, without refitting.

Run from the repo root with `.venv/bin/python scripts/audit_profit_uncertainty.py`.
This is an independent test-data access: pipeline.evaluate's log does not capture it.
Do not use these results for model/depth selection or call them a fresh evaluation.
"""

import json

import joblib
import numpy as np

from uplift_engine.config import load_config
from uplift_engine.data import load_experiment, load_features, make_split
from uplift_engine.policy import profit_at
from uplift_engine.warehouse import connect


def main() -> None:
    cfg = load_config()
    with connect(cfg, read_only=True) as con:
        experiment = load_experiment(con)
        split = make_split(experiment, cfg.experiment.test_share, cfg.experiment.seed)
        test = experiment[split == "test"]
        features = load_features(con, test.index)
        recorded = con.execute(
            "SELECT chosen_depth FROM ml.test_results WHERE role = 'champion'"
        ).fetchone()
    selection = joblib.load(cfg.paths.artifacts_dir / "selection.joblib")
    if recorded is None or recorded[0] != selection["depth"]:
        raise ValueError("Selection artifact does not match recorded test depth")
    score = joblib.load(cfg.paths.artifacts_dir / "model_dev.joblib").predict(features)
    treatment, outcome = test.treatment.to_numpy(), test.target.to_numpy()
    depth, margin = selection["depth"], selection["margin"]
    rng = np.random.default_rng(73)
    arms = [np.flatnonzero(treatment == arm) for arm in (0, 1)]
    draws = []
    for _ in range(1000):
        indices = np.concatenate([rng.choice(a, len(a), replace=True) for a in arms])
        y, s, t = outcome[indices], score[indices], treatment[indices]
        targeted = profit_at(y, s, t, depth, margin, cfg.economics.sms_cost)
        blanket = profit_at(y, s, t, 1.0, margin, cfg.economics.sms_cost)
        draws.append([targeted, blanket, targeted - blanket])
    values = np.asarray(draws)
    print(
        json.dumps(
            {
                "purpose": "audit-only fixed-policy diagnostic on previously evaluated test",
                "resampling": "customers within treatment arms; shared policy draws",
                "seed": 73,
                "n_bootstrap": 1000,
                "depth": depth,
                "columns": ["targeted", "blanket", "paired_advantage"],
                "units": "RUB per 1000 clients in the base",
                "percentile_95_interval_rows": np.quantile(values, [0.025, 0.975], axis=0).tolist(),
                "fraction_positive_paired_advantage": float((values[:, 2] > 0).mean()),
                "limitations": "fixed model; excludes training/selection uncertainty; reused test",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
