"""Pipeline stages. All write run-tagged history into the warehouse and files into the release.

The policy procedure (`fit_procedure`), defined once and used by both `assess` and `deploy`:

    1. inner K-fold cross-fitting of every candidate learner on the training customers
    2. champion = uplift learner or top-k ensemble with the highest inner out-of-fold Qini
    3. calibrator = isotonic map from the champion's inner OOF scores to observed uplift
    4. learner refitted on all training customers
    5. action: text a customer iff calibrated effect x margin > sms_cost (strictly greater)
    6. comparator: response model, refitted; texts the top share chosen on its own inner OOF
       value curve (so it is optimised on training data too, not forced to the uplift share)

Stages:
    assess      retrospective internal validation of the whole procedure in outer folds
    deploy      the procedure on all labelled customers -> bundle, scores, frozen campaign
    historical  corrected intervals for the reused 2026-10-24 test split (frozen scores)
    explain     surrogate (held-out fidelity) + exact S-learner log-odds attribution check
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field

import duckdb
import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import ks_2samp
from sklearn.model_selection import StratifiedKFold

from uplift_engine.config import UPLIFT_LEARNERS, Config
from uplift_engine.contract import FeatureContract
from uplift_engine.data import cv_folds, load_experiment, load_features
from uplift_engine.evaluation import metrics as m
from uplift_engine.lineage import LineageMismatchError, Run, require_fresh_features, sha256_file
from uplift_engine.models.learners import UpliftCalibrator, build_learner
from uplift_engine.monitoring import balance_table, psi_table
from uplift_engine.policy import (
    best_depth,
    break_even,
    evaluate_policies,
    margin_per_purchase,
    policy_value,
    profit_curve,
    select_by_effect,
    select_top,
    sensitivity,
)
from uplift_engine.release import building_release, latest_runs, release_dir
from uplift_engine.warehouse import append_run

DEPTH_KS = (0.1, 0.2, 0.3, 0.5)
BREAK_EVENS = [0.005, 0.01, 0.02, 0.03, 0.04, 0.05, 0.075, 0.1]
SHARE_GRID = np.round(np.arange(0.0, 1.0001, 0.01), 2)
ACTION_SEMANTICS = (
    "eligible iff calibrated_effect * margin_per_purchase > sms_cost "
    "(strict); campaign action = holdout if eligible and "
    "hash(campaign_id:client_id) < holdout_share, send if eligible otherwise, "
    "else ineligible"
)
METRIC_NAMES = ["qini_auc"] + [f"uplift_at_{int(k * 100)}" for k in DEPTH_KS]


# --------------------------------------------------------------------------- the procedure
@dataclass
class Procedure:
    champion: str
    learner: object
    calibrator: UpliftCalibrator
    response_model: object
    response_share: float
    oof: dict[str, np.ndarray] = field(repr=False)
    inner_qini: dict[str, float] = field(default_factory=dict)


def _crossfit(name, X, t, y, folds, params, seed) -> np.ndarray:
    oof = np.zeros(len(y))
    for fit_idx, pred_idx in folds:
        learner = build_learner(name, params, seed).fit(X.iloc[fit_idx], t[fit_idx], y[fit_idx])
        oof[pred_idx] = learner.predict(X.iloc[pred_idx])
    return oof


def _best_share(y, score, t, margin, cost) -> float:
    """Share with the highest point-estimated value on a ranking (0 = no-send if none > 0)."""
    depths = SHARE_GRID[1:]
    u = m._uplift_by_depth(y, score, t, depths)
    value = depths * (margin * u - cost)
    if np.all(np.isnan(value)) or np.nanmax(value) <= 0:
        return 0.0
    return float(depths[int(np.nanargmax(value))])


def fit_procedure(
    X: pd.DataFrame, t: np.ndarray, y: np.ndarray, cfg: Config, margin: float, log=print
) -> Procedure:
    seed, params = cfg.experiment.seed, cfg.models.lightgbm
    folds = cv_folds(t, y, cfg.experiment.n_folds, seed)
    oof = {}
    for name in cfg.models.candidates:
        start = time.perf_counter()
        oof[name] = _crossfit(name, X, t, y, folds, params, seed)
        log(f"    inner OOF {name:22s} {time.perf_counter() - start:5.0f}s")
    yf, tf = y.astype(float), t.astype(float)
    qini = {n: m._qini_auc(yf, s, tf) for n, s in oof.items()}
    uplift = sorted((n for n in oof if n in UPLIFT_LEARNERS), key=qini.get, reverse=True)
    k = cfg.models.ensemble_top_k
    if k:
        members = sorted(uplift[:k])
        ens = "ensemble:" + "+".join(members)
        oof[ens] = np.mean([oof[n] for n in members], axis=0)
        qini[ens] = m._qini_auc(yf, oof[ens], tf)
    contenders = [n for n in oof if n not in ("random", "response")]
    champion = max(contenders, key=qini.get)

    calibrator = UpliftCalibrator(cfg.calibration.n_bins).fit(oof[champion], t, y)
    learner = build_learner(champion, params, seed).fit(X, t, y)
    if "response" in oof:
        response_share = _best_share(yf, oof["response"], tf, margin, cfg.economics.sms_cost)
        response_model = build_learner("response", params, seed).fit(X, t, y)
    else:
        response_share, response_model = 0.0, None
    return Procedure(champion, learner, calibrator, response_model, response_share, oof, qini)


def _ranking_replicates(y, scores: dict[str, np.ndarray], t, draws: m.ArmBootstrap):
    """Qini and uplift@k for every model on the *same* draws (for intervals and paired diffs)."""
    ks = np.array(DEPTH_KS)

    def stat(yy, ss, tt):
        try:
            q = m._qini_auc(yy, ss, tt)
        except m.UndefinedMetricError:
            q = np.nan
        return np.r_[q, m._uplift_by_depth(yy, ss, tt, ks)]

    point = {n: stat(y, s, t) for n, s in scores.items()}
    reps = {n: np.array([stat(y[i], s[i], t[i]) for i in draws.indices]) for n, s in scores.items()}
    return point, reps


def _ranking_table(point, reps, draws, reference: str | None) -> pd.DataFrame:
    rows = []
    for name in point:
        row = {"model": name}
        for j, metric in enumerate(METRIC_NAMES):
            ci = draws.interval(point[name][j], reps[name][:, j])
            row |= {metric: ci.estimate, f"{metric}_low": ci.low, f"{metric}_high": ci.high}
        if reference and name != reference:
            d = reps[reference][:, 0] - reps[name][:, 0]
            ci = draws.interval(point[reference][0] - point[name][0], d)
            ok = d[~np.isnan(d)]
            row |= {
                "qini_lead_of_reference": ci.estimate,
                "qini_lead_low": ci.low,
                "qini_lead_high": ci.high,
                "frac_reference_ahead": float((ok > 0).mean()) if len(ok) else np.nan,
            }
        rows.append(row)
    return pd.DataFrame(rows)


def _curve_points(y, score, t, model: str, source: str, n_points: int = 101) -> pd.DataFrame:
    x, q = m._qini_curve(y, score, t)
    frac = np.linspace(0, 1, n_points)
    return pd.DataFrame(
        {
            "model": model,
            "source": source,
            "fraction": frac,
            "incremental_buyers": np.interp(frac * x[-1], x, q),
        }
    )


def _reliability(effect, y, t, n_bins: int = 10) -> pd.DataFrame:
    """Predicted (calibrated) vs observed uplift in quantile bins of the predicted effect."""
    df = pd.DataFrame({"e": effect, "y": y, "t": t})
    df["bin"] = pd.qcut(df.e.rank(method="first"), n_bins, labels=False) + 1
    rows = []
    for b, g in df.groupby("bin"):
        n1, n0 = int((g.t == 1).sum()), int((g.t == 0).sum())
        rt = g.y[g.t == 1].mean() if n1 else np.nan
        rc = g.y[g.t == 0].mean() if n0 else np.nan
        se = np.sqrt(rt * (1 - rt) / n1 + rc * (1 - rc) / n0) if n1 and n0 else np.nan
        rows.append(
            {
                "bin": int(b),
                "n": len(g),
                "n_treated": n1,
                "n_control": n0,
                "predicted_effect": g.e.mean(),
                "observed_uplift": rt - rc,
                "observed_se": se,
            }
        )
    return pd.DataFrame(rows)


def _labelled(con):
    experiment = load_experiment(con)
    return experiment, load_features(con, experiment.index)


def _write_assignment(con, run_id, series: pd.Series, name: str) -> str:
    frame = series.rename("fold").reset_index()
    digest = hashlib.sha256(
        pd.util.hash_pandas_object(frame, index=False).values.tobytes()
    ).hexdigest()
    append_run(con, frame.assign(assignment=name), "ml.fold_assignment", run_id)
    return digest


def _safe_value(y, t, sel, margin, cost) -> float:
    try:
        return policy_value(y, t, sel, margin, cost)
    except m.UndefinedMetricError:
        return np.nan


# --------------------------------------------------------------------------- assess
def assess(cfg: Config, con: duckdb.DuckDBPyConnection, log=print) -> dict:
    """Outer-fold retrospective validation of the complete procedure (see module docstring)."""
    release_id = building_release(cfg, con)
    econ, seed = cfg.economics, cfg.experiment.seed
    with Run(con, cfg, "assess", release_id) as run:
        margin = margin_per_purchase(econ, con)
        experiment, X = _labelled(con)
        t, y = experiment.treatment.to_numpy(), experiment.target.to_numpy()
        outer = StratifiedKFold(cfg.experiment.outer_folds, shuffle=True, random_state=seed + 1)
        fold_of = pd.Series(-1, index=experiment.index, name="fold")
        raw_all, eff_all = np.zeros(len(y)), np.zeros(len(y))
        dep_all, resp_all = np.zeros(len(y), bool), np.zeros(len(y), bool)
        fold_rows, transfer_rows, calib_rows, select_rows = [], [], [], []

        for k, (tr, va) in enumerate(outer.split(X, t * 2 + y)):
            log(
                f"  outer fold {k + 1}/{cfg.experiment.outer_folds}: "
                f"train {len(tr):,}, validate {len(va):,}"
            )
            fold_of.iloc[va] = k
            proc = fit_procedure(X.iloc[tr], t[tr], y[tr], cfg, margin, log)
            raw_va = proc.learner.predict(X.iloc[va])
            eff_va = proc.calibrator.transform(raw_va)
            dep_va = select_by_effect(eff_va, margin, econ.sms_cost)
            resp_va = (
                select_top(proc.response_model.predict(X.iloc[va]), proc.response_share)
                if proc.response_model is not None
                else np.zeros(len(va), bool)
            )
            raw_all[va], eff_all[va], dep_all[va], resp_all[va] = raw_va, eff_va, dep_va, resp_va

            inner_raw = proc.oof[proc.champion]
            ks = ks_2samp(inner_raw, raw_va)
            transfer_rows.append(
                {
                    "fold": k,
                    "champion": proc.champion,
                    "ks_statistic_oof_vs_refit": float(ks.statistic),
                    "ks_pvalue": float(ks.pvalue),
                    "oof_score_median": float(np.median(inner_raw)),
                    "refit_score_median": float(np.median(raw_va)),
                    "oof_score_iqr": float(np.subtract(*np.percentile(inner_raw, [75, 25]))),
                    "refit_score_iqr": float(np.subtract(*np.percentile(raw_va, [75, 25]))),
                    "eligible_share_inner_oof": float(
                        select_by_effect(
                            proc.calibrator.transform(inner_raw), margin, econ.sms_cost
                        ).mean()
                    ),
                    "eligible_share_validation": float(dep_va.mean()),
                }
            )
            calib_rows.append(_reliability(eff_va, y[va], t[va]).assign(fold=str(k)))
            select_rows += [
                {"fold": k, "model": n, "inner_oof_qini": q, "is_champion": n == proc.champion}
                for n, q in proc.inner_qini.items()
            ]
            fold_rows.append(
                {
                    "fold": k,
                    "n_train": len(tr),
                    "n_validate": len(va),
                    "champion": proc.champion,
                    "response_share_chosen_on_train": proc.response_share,
                    "deployed_share": float(dep_va.mean()),
                    "deployed_value_per_1000": _safe_value(
                        y[va], t[va], dep_va, margin, econ.sms_cost
                    ),
                    "response_value_per_1000": _safe_value(
                        y[va], t[va], resp_va, margin, econ.sms_cost
                    ),
                    "blanket_value_per_1000": _safe_value(
                        y[va], t[va], np.ones(len(va), bool), margin, econ.sms_cost
                    ),
                    "validation_qini_raw": m._qini_auc(
                        y[va].astype(float), raw_va, t[va].astype(float)
                    ),
                }
            )

        split_fp = _write_assignment(con, run.run_id, fold_of, "assessment_outer_fold")
        draws = m.arm_bootstrap(t, cfg.experiment.n_bootstrap, seed)
        values, diffs = evaluate_policies(
            y,
            t,
            {"deployed": dep_all, "response_optimized": resp_all},
            margin,
            econ.sms_cost,
            draws,
            references=("blanket", "no_send", "response_optimized"),
            random_shares={"random_expected": float(dep_all.mean())},
        )
        point, reps = _ranking_replicates(
            y.astype(float), {"champion_pooled": raw_all}, t.astype(float), draws
        )
        ranking = _ranking_table(point, reps, draws, None)
        calib = pd.concat([_reliability(eff_all, y, t).assign(fold="pooled"), *calib_rows])

        meta = json.dumps(
            {
                "source": "outer-fold retrospective internal validation",
                "population": f"all {len(y):,} labelled experiment customers, each validated "
                "once by a procedure fitted without its fold",
                "margin": margin,
                "sms_cost": econ.sms_cost,
                "split_fingerprint": split_fp,
                **draws.spec,
            }
        )
        for df, table in (
            (values, "assessment_policies"),
            (diffs, "assessment_differences"),
            (pd.DataFrame(fold_rows), "assessment_folds"),
            (calib, "assessment_calibration"),
            (pd.DataFrame(transfer_rows), "assessment_transfer"),
            (pd.DataFrame(select_rows), "assessment_selection"),
            (ranking, "assessment_ranking"),
        ):
            append_run(con, df.assign(meta=meta), f"ml.{table}", run.run_id)
        pd.DataFrame(
            {
                "client_id": experiment.index,
                "fold": fold_of.to_numpy(),
                "raw": raw_all,
                "effect": eff_all,
                "deployed": dep_all,
                "response": resp_all,
                "treatment": t,
                "target": y,
                "producer_run_id": run.run_id,
            }
        ).to_parquet(release_dir(cfg, release_id) / "assessment_predictions.parquet")
        run.details = {
            "split_fingerprint": split_fp,
            "champions": [r["champion"] for r in fold_rows],
        }
        log(values.to_string(index=False))
        log(diffs.to_string(index=False))
        return {"values": values, "differences": diffs, "folds": pd.DataFrame(fold_rows)}


# --------------------------------------------------------------------------- deploy
def holdout_draw(campaign_id: str, client_ids) -> np.ndarray:
    """Deterministic uniform [0, 1) per (campaign, client): order- and population-independent."""
    return np.array(
        [
            int(hashlib.sha256(f"{campaign_id}:{c}".encode()).hexdigest()[:16], 16) / 2**64
            for c in client_ids
        ]
    )


def assign_campaign(
    client_ids, eligible, campaign_id: str, holdout_share: float, policy_version: str
) -> pd.DataFrame:
    u = holdout_draw(campaign_id, client_ids)
    eligible = np.asarray(eligible, dtype=bool)
    action = np.where(~eligible, "ineligible", np.where(u < holdout_share, "holdout", "send"))
    return pd.DataFrame(
        {
            "client_id": list(client_ids),
            "campaign_id": campaign_id,
            "policy_version": policy_version,
            "eligible": eligible,
            "action": action,
            "holdout_probability": holdout_share,
            "assignment_draw": u,
        }
    )


def deploy(cfg: Config, con: duckdb.DuckDBPyConnection, log=print) -> pd.DataFrame:
    release_id = building_release(cfg, con)
    econ, seed, camp = cfg.economics, cfg.experiment.seed, cfg.campaign
    rdir = release_dir(cfg, release_id)
    with Run(con, cfg, "deploy", release_id) as run:
        margin = margin_per_purchase(econ, con)
        build = require_fresh_features(con, cfg)
        experiment = load_experiment(con)
        X_all = load_features(con)
        contract = FeatureContract.from_training(X_all)
        X_lab = X_all.loc[experiment.index]
        t, y = experiment.treatment.to_numpy(), experiment.target.to_numpy()

        log("  fitting the policy procedure on all labelled customers")
        proc = fit_procedure(X_lab, t, y, cfg, margin, log)
        draws = m.arm_bootstrap(t, cfg.experiment.n_bootstrap, seed)
        yf, tf = y.astype(float), t.astype(float)
        point, reps = _ranking_replicates(yf, proc.oof, tf, draws)
        board = _ranking_table(point, reps, draws, proc.champion)
        board["inner_oof_qini_used_for_selection"] = board.model.map(proc.inner_qini)
        board["is_champion"] = board.model == proc.champion
        board["is_baseline"] = board.model.isin(["random", "response"])
        board = board.sort_values("qini_auc", ascending=False)
        board["source"] = "all-labelled inner OOF (the selection data: optimistic for the champion)"
        lo, hi = m.random_noise_floor(yf, tf, cfg.experiment.noise_rankings, seed)
        board["noise_band_low"], board["noise_band_high"] = lo, hi

        control = lgb.LGBMClassifier(**cfg.models.lightgbm, random_state=seed).fit(
            X_lab[t == 0], y[t == 0]
        )
        log("  scoring all customers")
        raw = proc.learner.predict(X_all)
        effect = proc.calibrator.transform(raw)
        p0 = control.predict_proba(X_all)[:, 1]
        eligible = select_by_effect(effect, margin, econ.sms_cost)
        be = break_even(margin, econ.sms_cost)

        bundle = {
            "release_id": release_id,
            "deploy_run_id": run.run_id,
            "champion": proc.champion,
            "learner": proc.learner,
            "calibrator": proc.calibrator,
            "control_model": control,
            "contract": contract,
            "margin": margin,
            "sms_cost": econ.sms_cost,
            "break_even": be,
            "action_semantics": ACTION_SEMANTICS,
            "feature_build_id": build["build_id"],
            "dataset_fp": build["dataset_fp"],
            "schema_fp": build["schema_fp"],
            "config_hash": run.lineage["config_hash"],
            "campaign_id": camp.campaign_id,
            "holdout_share": camp.holdout_share,
            "lineage": run.record,
        }
        joblib.dump(bundle, rdir / "bundle.joblib")
        policy_version = sha256_file(rdir / "bundle.joblib")[:16]

        scores = pd.DataFrame(
            {
                "client_id": X_all.index,
                "uplift_score": raw,
                "calibrated_effect": effect,
                "p_buy_without_sms": p0,
                "p_buy_with_sms_model": np.clip(p0 + effect, 0, 1),
                "economically_eligible": eligible,
                "expected_value_if_texted": effect * margin - econ.sms_cost,
            }
        )
        scores["rank"] = scores.uplift_score.rank(method="first", ascending=False).astype(int)
        scores["decile"] = np.ceil(scores["rank"] / len(scores) * 10).astype(int)
        scores["predicted_group"] = np.select(
            [effect <= 0, eligible, p0 >= 0.7],
            ["Predicted do-not-disturb", "Predicted persuadable", "Predicted sure thing"],
            default="Predicted lost cause",
        )
        scores["in_experiment"] = scores.client_id.isin(experiment.index)
        oof_rank = pd.Series(proc.oof[proc.champion], index=experiment.index).rank(
            method="first", ascending=False
        )
        scores["oof_decile"] = scores.client_id.map(
            np.ceil(oof_rank / len(oof_rank) * 10).astype(int)
        ).astype("Int64")
        scores["policy_version"] = policy_version
        campaign = assign_campaign(
            scores.client_id, eligible, camp.campaign_id, camp.holdout_share, policy_version
        )
        scores = scores.merge(campaign[["client_id", "action"]], on="client_id")
        scores["producer_run_id"] = run.run_id
        campaign["producer_run_id"] = run.run_id
        scores.to_parquet(rdir / "scores.parquet")
        campaign.to_parquet(rdir / "campaign_assignment.parquet")

        curve = profit_curve(yf, proc.oof[proc.champion], tf, margin, econ.sms_cost, draws)
        sent = scores.action == "send"
        policy = pd.DataFrame(
            [
                {
                    "policy_version": policy_version,
                    "champion": proc.champion,
                    "currency": econ.currency,
                    "sms_cost": econ.sms_cost,
                    "margin_per_purchase": margin,
                    "break_even_uplift": be,
                    "action_semantics": ACTION_SEMANTICS,
                    "campaign_id": camp.campaign_id,
                    "holdout_share": camp.holdout_share,
                    "n_scored": len(scores),
                    "n_eligible": int(eligible.sum()),
                    "n_send": int(sent.sum()),
                    "n_holdout": int((scores.action == "holdout").sum()),
                    "response_share_chosen": proc.response_share,
                    "projected_additional_buyers_sent": float(
                        scores.loc[sent, "calibrated_effect"].sum()
                    ),
                    "projected_value_sent": float(
                        scores.loc[sent, "expected_value_if_texted"].sum()
                    ),
                    "projected_value_blanket": float(
                        len(scores) * (m.ate(y, t) * margin - econ.sms_cost)
                    ),
                    "ate_experiment": m.ate(y, t),
                    "projection_basis": (
                        "sum of calibrated model effects x assumed margin - SMS cost over "
                        "customers actually texted (holdout excluded); a model-based "
                        "scenario, not an observed result"
                    ),
                    "oof_curve_estimated_best_share": best_depth(curve),
                }
            ]
        )
        spec = {"bootstrap_" + k: str(v) for k, v in draws.spec.items()}

        append_run(con, board, "ml.leaderboard", run.run_id)
        append_run(
            con,
            pd.concat(
                [_curve_points(yf, s, tf, n, "all-labelled inner OOF") for n, s in proc.oof.items()]
            ),
            "ml.qini_curves",
            run.run_id,
        )
        append_run(
            con,
            m.decile_table(yf, proc.oof[proc.champion], tf).assign(
                model=proc.champion, source="all-labelled inner OOF"
            ),
            "ml.decile_calibration",
            run.run_id,
        )
        append_run(
            con,
            curve.assign(model=proc.champion, source="all-labelled inner OOF", **spec),
            "ml.profit_curves",
            run.run_id,
        )
        append_run(con, policy, "ml.policy", run.run_id)
        append_run(con, sensitivity(curve, margin, BREAK_EVENS), "ml.sensitivity", run.run_id)
        append_run(
            con,
            proc.calibrator.bins_.reset_index().assign(
                calibrated=proc.calibrator.transform(proc.calibrator.bins_.score)
            ),
            "ml.calibration_bins",
            run.run_id,
        )
        append_run(con, scores, "ml.scores", run.run_id)
        append_run(con, campaign, "ml.campaign_assignment", run.run_id)
        unlabelled = X_all.loc[~X_all.index.isin(experiment.index)]
        append_run(con, psi_table(X_lab, unlabelled, cfg.monitoring), "ml.drift", run.run_id)
        append_run(con, balance_table(X_lab, experiment.treatment), "ml.balance", run.run_id)
        run.details = {
            "policy_version": policy_version,
            "champion": proc.champion,
            "bundle_sha256": sha256_file(rdir / "bundle.joblib"),
        }
        log(
            f"  champion {proc.champion}; eligible {eligible.mean():.1%}; "
            f"send {sent.sum():,}; holdout {(scores.action == 'holdout').sum():,}"
        )
        return scores


# --------------------------------------------------------------------------- historical
def historical_available(cfg: Config) -> tuple[bool, str]:
    """Whether the frozen historical evidence exists locally and matches its committed manifest.

    It is optional: a release without it is complete, and shows historical evidence as
    unavailable. Checked before any expensive stage runs.
    """
    snap, mpath = cfg.paths.historical_snapshot, cfg.paths.historical_manifest
    path = snap / "historical_test_scores.parquet"
    if not mpath.exists():
        return False, f"manifest {mpath} not found"
    if not path.exists():
        return False, (
            f"frozen historical scores {path} are not present (they live in a local, "
            "gitignored evidence snapshot)"
        )
    manifest = json.loads(mpath.read_text())
    entry = manifest.get("files", {}).get("historical_test_scores.parquet", {})
    if sha256_file(path) != entry.get("sha256"):
        return False, f"{path} does not match the committed manifest"
    return True, "frozen historical scores verified against the committed manifest"


def historical(cfg: Config, con: duckdb.DuckDBPyConnection, log=print) -> pd.DataFrame:
    """Corrected intervals for the 2026-10-24 bundle on the reused test split.

    Uses the per-customer scores frozen in the evidence snapshot (verified against the committed
    manifest); does not read features or refit. Policies are the historical ones: champion and
    response at the champion's development-chosen 41% depth, and the expected random policy.
    """
    release_id = building_release(cfg, con)
    snap, mpath = cfg.paths.historical_snapshot, cfg.paths.historical_manifest
    with Run(con, cfg, "historical", release_id) as run:
        manifest = json.loads(mpath.read_text())
        path = snap / "historical_test_scores.parquet"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing: the historical snapshot is not available")
        if sha256_file(path) != manifest["files"]["historical_test_scores.parquet"]["sha256"]:
            raise LineageMismatchError(f"{path} does not match {mpath}")
        info = manifest["historical_test_scores"]
        s = pd.read_parquet(path)
        y, t = s.target.to_numpy().astype(float), s.treatment.to_numpy().astype(float)
        draws = m.arm_bootstrap(t, cfg.experiment.n_bootstrap, cfg.experiment.seed)
        scores = {
            "champion": s.champion.to_numpy(),
            "response": s.response.to_numpy(),
            "random_draw": s.random.to_numpy(),
        }
        point, reps = _ranking_replicates(y, scores, t, draws)
        table = _ranking_table(point, reps, draws, "champion")
        lo, hi = m.random_noise_floor(y, t, cfg.experiment.noise_rankings, cfg.experiment.seed)
        depth, margin = info["dev_chosen_depth"], info["margin"]
        values, diffs = evaluate_policies(
            y,
            t,
            {
                "champion_at_41pct": select_top(scores["champion"], depth),
                "response_at_41pct": select_top(scores["response"], depth),
            },
            margin,
            cfg.economics.sms_cost,
            draws,
            references=("blanket", "no_send", "response_at_41pct"),
            random_shares={"random_expected_41pct": depth},
        )
        n_access = con.execute("SELECT count(*) FROM meta.test_access_log").fetchone()[0]
        note = (
            f"historical test split (40,008 customers) of the 2026-10-24 bundle "
            f"({info['champion']}); scored {n_access} times by logged pipeline/remediation "
            "calls and reused by the 2026-10-25 audit: a reused holdout, not independent "
            "confirmation"
        )
        table = table.assign(
            source=note, n=len(y), noise_band_low=lo, noise_band_high=hi, ate=m.ate(y, t)
        )
        append_run(con, table, "ml.historical_test", run.run_id)
        append_run(
            con, values.assign(source=note, margin=margin), "ml.historical_policies", run.run_id
        )
        append_run(con, diffs.assign(source=note), "ml.historical_differences", run.run_id)
        append_run(
            con,
            pd.concat(
                [_curve_points(y, sc, t, n, "historical reused test") for n, sc in scores.items()]
            ),
            "ml.qini_curves",
            run.run_id,
        )
        run.details = {"snapshot_scores_sha256": info["sha256"], "test_accesses": n_access}
        log(table[["model", "qini_auc", "qini_auc_low", "qini_auc_high"]].to_string(index=False))
        return table


# --------------------------------------------------------------------------- explain
def load_release_bundle(cfg: Config, con, release_id: str) -> dict:
    path = release_dir(cfg, release_id) / "bundle.joblib"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing; run `ute deploy` for release {release_id}")
    bundle = joblib.load(path)
    build = require_fresh_features(con, cfg)
    if bundle["feature_build_id"] != build["build_id"]:
        raise LineageMismatchError(
            f"bundle was trained on feature build {bundle['feature_build_id']}, but the "
            f"warehouse now holds {build['build_id']}; rerun `ute deploy`"
        )
    deploy = latest_runs(con, release_id).get("deploy")
    if (
        deploy is None
        or deploy["status"] != "succeeded"
        or bundle.get("deploy_run_id") != deploy["run_id"]
    ):
        raise LineageMismatchError(
            f"bundle.joblib was produced by {bundle.get('deploy_run_id')}, which is not the "
            f"release's latest successful deploy run; rerun `ute deploy`"
        )
    return bundle


def explain(
    cfg: Config, con: duckdb.DuckDBPyConnection, n_rows: int = 50_000, log=print
) -> pd.DataFrame:
    """Attribution of the *raw uplift score* (not the calibrated effect or profit).

    A LightGBM surrogate is fitted on 70% of a sample and its fidelity measured on the other 30%
    (held-out R^2, RMSE, error by score decile). TreeSHAP of the surrogate on the held-out rows
    gives the importance shown in the dashboard. For an S-learner champion, exact treatment-on
    minus treatment-off TreeSHAP contributions are also computed on the log-odds scale, where they
    are additive, and the additivity error is recorded.
    """
    release_id = building_release(cfg, con)
    seed = cfg.experiment.seed
    bundle = load_release_bundle(cfg, con, release_id)
    with Run(con, cfg, "explain", release_id, parents=[bundle["deploy_run_id"]]) as run:
        X_all = load_features(con)
        sample = X_all.sample(min(n_rows, len(X_all)), random_state=seed)
        target = bundle["learner"].predict(sample)
        is_fit = np.random.default_rng(seed).random(len(sample)) < 0.7
        surrogate = lgb.LGBMRegressor(
            n_estimators=400, learning_rate=0.05, num_leaves=63, verbose=-1, random_state=seed
        )
        surrogate.fit(sample[is_fit], target[is_fit])

        def fidelity(mask, split):
            pred = surrogate.predict(sample[mask])
            err = target[mask] - pred
            r2 = 1 - np.sum(err**2) / np.sum((target[mask] - target[mask].mean()) ** 2)
            dec = (
                pd.qcut(pd.Series(target[mask]).rank(method="first"), 10, labels=False) + 1
            ).to_numpy()
            by = pd.DataFrame({"decile": dec, "abs_err": np.abs(err)}).groupby("decile").abs_err
            head = [
                {
                    "split": split,
                    "decile": "all",
                    "r2": float(r2),
                    "rmse": float(np.sqrt(np.mean(err**2))),
                    "mae": float(np.mean(np.abs(err))),
                    "n": int(mask.sum()),
                }
            ]
            return head + [
                {
                    "split": split,
                    "decile": str(d),
                    "r2": np.nan,
                    "rmse": np.nan,
                    "mae": float(v),
                    "n": int((dec == d).sum()),
                }
                for d, v in by.mean().items()
            ]

        fid = pd.DataFrame(
            fidelity(is_fit, "surrogate training rows") + fidelity(~is_fit, "held-out rows")
        )
        held_r2 = float(fid.query("split == 'held-out rows' and decile == 'all'").r2.iloc[0])

        Xh = sample[~is_fit]
        contrib = surrogate.predict(Xh, pred_contrib=True)[:, :-1]
        imp = np.abs(contrib).mean(axis=0)
        rows = []
        for j, col in enumerate(sample.columns):
            values = Xh[col].cat.codes if hasattr(Xh[col], "cat") else Xh[col]
            ok = values.notna().to_numpy()
            corr = (
                np.corrcoef(values[ok], contrib[ok, j])[0, 1]
                if ok.sum() > 2 and np.std(values[ok]) > 0
                else np.nan
            )
            rows.append(
                {
                    "feature": col,
                    "mean_abs_contribution": imp[j],
                    "share": imp[j] / imp.sum(),
                    "direction_corr": corr,
                    "missing_rate": 1 - ok.mean(),
                }
            )
        importance = pd.DataFrame(rows).sort_values("mean_abs_contribution", ascending=False)
        importance = importance.assign(
            method="surrogate TreeSHAP of the raw uplift score (held-out rows)",
            surrogate_heldout_r2=held_r2,
        )

        if bundle["champion"] == "s_learner":
            model = bundle["learner"].model_
            on, off = Xh.assign(_t=1), Xh.assign(_t=0)
            diff = model.predict(on, pred_contrib=True, raw_score=True) - model.predict(
                off, pred_contrib=True, raw_score=True
            )
            gap = model.predict(on, raw_score=True) - model.predict(off, raw_score=True)
            additivity = float(np.max(np.abs(diff.sum(axis=1) - gap)))
            exact = pd.DataFrame(
                {
                    "feature": [*sample.columns, "_t (treatment main effect)"],
                    "mean_abs_logodds_effect_contribution": np.abs(diff[:, :-1]).mean(axis=0),
                }
            ).assign(
                max_additivity_error=additivity,
                scale="log-odds treatment effect of the S-learner (not probability uplift)",
            )
            run.details["exact_additivity_error"] = additivity
            log(f"  exact S-learner log-odds attribution: max additivity error {additivity:.2e}")
        else:
            exact = pd.DataFrame(
                [
                    {
                        "feature": None,
                        "mean_abs_logodds_effect_contribution": np.nan,
                        "max_additivity_error": np.nan,
                        "scale": "not applicable: champion "
                        f"{bundle['champion']} is not an S-learner",
                    }
                ]
            )
        append_run(con, exact, "ml.shap_exact_check", run.run_id)

        append_run(con, importance, "ml.feature_importance", run.run_id)
        append_run(con, fid, "ml.surrogate_fidelity", run.run_id)
        dep = []
        for col in importance.feature.head(8):
            if hasattr(Xh[col], "cat"):
                continue
            j = list(sample.columns).index(col)
            frame = pd.DataFrame({"value": Xh[col].to_numpy(), "contribution": contrib[:, j]})
            frame = frame.dropna()
            frame["bin"] = pd.qcut(frame.value.rank(method="first"), 20, labels=False)
            agg = (
                frame.groupby("bin")
                .agg(
                    value=("value", "median"),
                    contribution=("contribution", "mean"),
                    n=("value", "size"),
                )
                .reset_index()
            )
            dep.append(agg.assign(feature=col))
        append_run(con, pd.concat(dep), "ml.feature_dependence", run.run_id)
        run.details["surrogate_heldout_r2"] = held_r2
        log(f"  surrogate held-out R^2 = {held_r2:.3f}")
        return importance
