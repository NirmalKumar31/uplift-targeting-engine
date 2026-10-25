"""The compact results artifact of a release (`results.json`).

Every headline number shown by the dashboard, the static site and the README is read from this
file, which is generated from the release's warehouse history - never typed into a presentation
file. Each block states its evaluation source and population.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from uplift_engine.release import stage_runs


def _records(df: pd.DataFrame) -> list[dict]:
    clean = df.drop(columns=[c for c in ("run_id", "meta") if c in df.columns])
    return json.loads(clean.to_json(orient="records", double_precision=10))


def _table(con, table: str, run_id: str) -> pd.DataFrame:
    return con.execute(f"SELECT * FROM {table} WHERE run_id = ?", [run_id]).df()


def build_results(con: duckdb.DuckDBPyConnection, release_id: str) -> dict:
    runs = {
        s: r["run_id"] for s, r in stage_runs(con, release_id).items() if r["status"] == "succeeded"
    }
    a, d, h, e = runs["assess"], runs["deploy"], runs["historical"], runs["explain"]

    pol = _table(con, "ml.policy", d).iloc[0]
    folds = _table(con, "ml.assessment_folds", a)
    calib = _table(con, "ml.assessment_calibration", a)
    pooled = calib[calib.fold == "pooled"]
    w = pooled.n / pooled.n.sum()
    ameta = json.loads(_table(con, "ml.assessment_policies", a).meta.iloc[0])
    fid = _table(con, "ml.surrogate_fidelity", e)
    exact = _table(con, "ml.shap_exact_check", e)
    hist_meta = _table(con, "ml.historical_test", h)
    access = con.execute(
        "SELECT accessed_at, champion, reason FROM meta.test_access_log ORDER BY accessed_at"
    ).df()

    return {
        "release_id": release_id,
        "policy_version": pol.policy_version,
        "generated_at": datetime.now(UTC).isoformat(),
        "runs": runs,
        "economics": {
            "currency": pol.currency,
            "sms_cost": pol.sms_cost,
            "margin_per_additional_buyer": pol.margin_per_purchase,
            "break_even_uplift": pol.break_even_uplift,
            "status": "assumed utility: constant historical basket x gross margin; not an "
            "identified causal revenue estimate",
        },
        "action_semantics": pol.action_semantics,
        "assessment": {
            "source": ameta["source"],
            "population": ameta["population"],
            "status": "retrospective internal validation on customers already used during "
            "earlier exploration; not independent confirmation",
            "bootstrap": {
                k: ameta[k]
                for k in (
                    "method",
                    "resampling_unit",
                    "strata",
                    "n_boot",
                    "seed",
                    "confidence",
                    "includes",
                )
            },
            "policies": _records(_table(con, "ml.assessment_policies", a)),
            "differences": _records(_table(con, "ml.assessment_differences", a)),
            "ranking": _records(_table(con, "ml.assessment_ranking", a)),
            "folds": _records(folds),
            "fold_value_range_deployed": [
                float(folds.deployed_value_per_1000.min()),
                float(folds.deployed_value_per_1000.max()),
            ],
            "champions_by_fold": folds.champion.tolist(),
            "calibration_pooled": {
                "weighted_mean_abs_gap": float(
                    (w * (pooled.predicted_effect - pooled.observed_uplift).abs()).sum()
                ),
                "bins": _records(
                    pooled[["bin", "n", "predicted_effect", "observed_uplift", "observed_se"]]
                ),
            },
            "transfer": _records(_table(con, "ml.assessment_transfer", a)),
            "fold_spread_note": "folds share training customers; their spread is descriptive, "
            "not independent replications",
        },
        "historical_test": {
            "source": hist_meta.source.iloc[0],
            "models": _records(hist_meta.drop(columns=["source"])),
            "policies": _records(_table(con, "ml.historical_policies", h).drop(columns=["source"])),
            "differences": _records(
                _table(con, "ml.historical_differences", h).drop(columns=["source"])
            ),
            "access_log": json.loads(access.to_json(orient="records", date_format="iso")),
        },
        "deployment_projection": {
            "status": pol.projection_basis,
            "champion": pol.champion,
            "n_scored": int(pol.n_scored),
            "n_eligible": int(pol.n_eligible),
            "n_send": int(pol.n_send),
            "n_holdout": int(pol.n_holdout),
            "campaign_id": pol.campaign_id,
            "holdout_share": pol.holdout_share,
            "projected_additional_buyers_sent": pol.projected_additional_buyers_sent,
            "projected_value_sent": pol.projected_value_sent,
            "projected_value_blanket": pol.projected_value_blanket,
        },
        "leaderboard": _records(
            _table(con, "ml.leaderboard", d)[
                [
                    "model",
                    "qini_auc",
                    "qini_auc_low",
                    "qini_auc_high",
                    "uplift_at_10",
                    "is_champion",
                    "is_baseline",
                    "source",
                ]
            ]
        ),
        "explainability": {
            "surrogate_fidelity": _records(fid[fid.decile == "all"]),
            "exact_s_learner_check": None
            if exact.feature.isna().all()
            else {
                "max_additivity_error": float(exact.max_additivity_error.iloc[0]),
                "scale": exact.scale.iloc[0],
            },
        },
    }


def write_results(con, release_id: str, path: Path) -> dict:
    res = build_results(con, release_id)
    path.write_text(json.dumps(res, indent=2, default=_default) + "\n")
    return res


def _default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return str(o)
