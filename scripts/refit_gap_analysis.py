"""Compare the eligible share of the full-data refit with outer-fold validation.

uv run python scripts/refit_gap_analysis.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

from uplift_engine.config import load_config
from uplift_engine.data import cv_folds, load_experiment, load_features
from uplift_engine.pipeline import _crossfit
from uplift_engine.policy import select_by_effect
from uplift_engine.release import active_release, latest_runs
from uplift_engine.warehouse import connect

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out", type=Path, default=ROOT / "docs" / "evidence" / "refit_gap_analysis.json"
    )
    args = ap.parse_args()
    cfg = load_config()
    rid, rdir = active_release(cfg)
    bundle = joblib.load(rdir / "bundle.joblib")
    cal, margin, cost = bundle["calibrator"], bundle["margin"], bundle["sms_cost"]
    be = cost / margin

    with connect(cfg, read_only=True) as con:
        experiment = load_experiment(con)
        X = load_features(con, experiment.index)
        X_unl = load_features(con)
        X_unl = X_unl.loc[~X_unl.index.isin(experiment.index)]
        assess_run = latest_runs(con, rid)["assess"]["run_id"]
        transfer = con.execute(
            "SELECT * FROM ml.assessment_transfer WHERE run_id = ?", [assess_run]
        ).df()
        folds = con.execute("SELECT * FROM ml.assessment_folds WHERE run_id = ?", [assess_run]).df()
    t, y = experiment.treatment.to_numpy(), experiment.target.to_numpy()

    oof = _crossfit(
        bundle["champion"],
        X,
        t,
        y,
        cv_folds(t, y, cfg.experiment.n_folds, cfg.experiment.seed),
        cfg.models.lightgbm,
        cfg.experiment.seed,
    )
    refit = bundle["learner"].predict(X)
    refit_unl = bundle["learner"].predict(X_unl)
    share = {
        "inner_oof_scores_labelled": float(
            select_by_effect(cal.transform(oof), margin, cost).mean()
        ),
        "refit_scores_labelled": float(select_by_effect(cal.transform(refit), margin, cost).mean()),
        "refit_scores_unlabelled": float(
            select_by_effect(cal.transform(refit_unl), margin, cost).mean()
        ),
    }
    ks = ks_2samp(oof, refit)
    q = [0.5, 0.75, 0.9, 0.95, 0.99]

    bins = cal.bins_.reset_index().assign(calibrated=cal.transform(cal.bins_.score))
    # the raw-score threshold where the calibrated effect first exceeds break-even
    grid = np.linspace(min(oof.min(), refit.min()), max(oof.max(), refit.max()), 4000)
    above = grid[cal.transform(grid) > be]
    threshold = float(above.min()) if len(above) else None
    sens = []
    for f in (0.75, 0.9, 0.95, 1.0, 1.05, 1.1, 1.25):
        b = be * f
        sens.append(
            {
                "break_even_factor": f,
                "break_even_uplift": b,
                "share_oof": float((cal.transform(oof) > b).mean()),
                "share_refit": float((cal.transform(refit) > b).mean()),
            }
        )

    result = {
        "release_id": rid,
        "champion": bundle["champion"],
        "break_even_uplift": be,
        "eligible_share": share,
        "score_distribution": {
            "ks_oof_vs_refit": float(ks.statistic),
            "oof_quantiles": dict(
                zip(map(str, q), np.quantile(oof, q).round(5).tolist(), strict=True)
            ),
            "refit_quantiles": dict(
                zip(map(str, q), np.quantile(refit, q).round(5).tolist(), strict=True)
            ),
            "share_refit_above_oof_threshold": float((refit >= threshold).mean())
            if threshold is not None
            else None,
            "share_oof_above_threshold": float((oof >= threshold).mean())
            if threshold is not None
            else None,
            "raw_score_threshold": threshold,
        },
        "calibrator_bins": json.loads(bins.to_json(orient="records")),
        "break_even_sensitivity": sens,
        "outer_folds": json.loads(
            folds[["fold", "champion", "deployed_share", "response_share_chosen_on_train"]].to_json(
                orient="records"
            )
        ),
        "outer_fold_transfer": json.loads(
            transfer[
                [
                    "fold",
                    "champion",
                    "ks_statistic_oof_vs_refit",
                    "eligible_share_inner_oof",
                    "eligible_share_validation",
                ]
            ].to_json(orient="records")
        ),
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: result[k]
                for k in ("eligible_share", "score_distribution", "break_even_sensitivity")
            },
            indent=2,
        )
    )
    print(pd.DataFrame(result["outer_folds"]).to_string(index=False))


if __name__ == "__main__":
    main()
