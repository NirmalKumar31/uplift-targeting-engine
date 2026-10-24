"""Pipeline stages. Each reads from and writes to the warehouse, so dbt marts can build on top.

train     cross-fit every candidate on the development set, pick the champion
evaluate  fit the champion on dev, score the locked test set (every access is logged)
deploy    cross-fit + refit on all labelled clients, calibrate, score all ~400k clients
explain   surrogate TreeSHAP of the deployed scores
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

from uplift_engine.config import REPO_ROOT, Config
from uplift_engine.data import cv_folds, load_experiment, load_features, make_split
from uplift_engine.evaluation import metrics as m
from uplift_engine.models.learners import UpliftCalibrator, build_learner
from uplift_engine.monitoring import balance_table, psi_table
from uplift_engine.policy import (
    best_depth,
    margin_per_purchase,
    profit_at,
    profit_curve,
    sensitivity,
)
from uplift_engine.warehouse import write_table

DEPTH_KS = (0.1, 0.2, 0.3, 0.5)
BREAK_EVENS = [0.005, 0.01, 0.02, 0.03, 0.04, 0.05, 0.075, 0.1]


# --------------------------------------------------------------------------- helpers
def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _config_hash(cfg: Config) -> str:
    return hashlib.sha256(cfg.model_dump_json().encode()).hexdigest()[:12]


def log_run(con: duckdb.DuckDBPyConnection, cfg: Config, stage: str, metrics: dict) -> str:
    run_id = hashlib.sha256(f"{stage}{time.time_ns()}".encode()).hexdigest()[:16]
    con.execute("CREATE SCHEMA IF NOT EXISTS ml")
    con.execute(
        "CREATE TABLE IF NOT EXISTS ml.runs (run_id VARCHAR, stage VARCHAR, started_at TIMESTAMP,"
        " git_sha VARCHAR, config_hash VARCHAR, metrics JSON)"
    )
    con.execute(
        "INSERT INTO ml.runs VALUES (?, ?, ?, ?, ?, ?)",
        [
            run_id,
            stage,
            datetime.now(UTC),
            _git_sha(),
            _config_hash(cfg),
            json.dumps(metrics, default=float),
        ],
    )
    return run_id


def _curve_points(y, score, t, model: str, split: str, n_points: int = 101) -> pd.DataFrame:
    """Qini curve resampled at fixed population fractions (small enough for a BI table)."""
    x, q = m.qini_curve(y, score, t)
    frac = np.linspace(0, 1, n_points)
    return pd.DataFrame(
        {
            "model": model,
            "split": split,
            "fraction": frac,
            "incremental_buyers": np.interp(frac * x[-1], x, q),
        }
    )


def _ranking_report(y, score, t, n_boot: int, seed: int) -> dict:
    """Qini AUC and uplift@k with bootstrap CIs, all metrics computed on shared resamples."""
    ks = np.array(DEPTH_KS)
    names = ["qini_auc"] + [f"uplift_at_{int(k * 100)}" for k in ks]
    stat = lambda a, b, c: np.r_[m.qini_auc(a, b, c), m.uplift_by_depth(a, b, c, ks)]  # noqa: E731
    y, score, t = (np.asarray(a, float) for a in (y, score, t))
    point = stat(y, score, t)
    rng = np.random.default_rng(seed)
    boots = np.array(
        [
            stat(y[i], score[i], t[i])
            for i in (m._stratified_resample(t, y, rng) for _ in range(n_boot))
        ]
    )
    lo, hi = np.nanquantile(boots, [0.025, 0.975], axis=0)
    out = {}
    for j, name in enumerate(names):
        out |= {name: point[j], f"{name}_low": lo[j], f"{name}_high": hi[j]}
    return out


def _crossfit(name, X, t, y, folds, params, seed) -> np.ndarray:
    oof = np.zeros(len(y))
    for fit_idx, pred_idx in folds:
        learner = build_learner(name, params, seed).fit(X.iloc[fit_idx], t[fit_idx], y[fit_idx])
        oof[pred_idx] = learner.predict(X.iloc[pred_idx])
    return oof


def _labelled(con, cfg: Config):
    experiment = load_experiment(con)
    split = make_split(experiment, cfg.experiment.test_share, cfg.experiment.seed)
    return experiment, split


def artifact_path(cfg: Config, name: str) -> Path:
    cfg.paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    return cfg.paths.artifacts_dir / name


# --------------------------------------------------------------------------- stages
def train(cfg: Config, con: duckdb.DuckDBPyConnection, log=print) -> pd.DataFrame:
    """Cross-fit all candidates on dev, build the ensemble, rank on out-of-fold Qini."""
    seed, exp_cfg = cfg.experiment.seed, cfg.experiment
    experiment, split = _labelled(con, cfg)
    write_table(con, split.reset_index(), "ml.split_assignment")
    write_table(
        con, balance_table(load_features(con, experiment.index), experiment.treatment), "ml.balance"
    )

    dev = experiment[split == "dev"]
    X = load_features(con, dev.index)
    t, y = dev.treatment.to_numpy(), dev.target.to_numpy()
    folds = cv_folds(t, y, exp_cfg.n_folds, seed)
    params = cfg.models.lightgbm

    oof, seconds = {}, {}
    for name in cfg.models.candidates:
        start = time.perf_counter()
        oof[name] = _crossfit(name, X, t, y, folds, params, seed)
        seconds[name] = time.perf_counter() - start
        log(f"  {name:22s} OOF Qini {m.qini_auc(y, oof[name], t):+.4f}  ({seconds[name]:.0f}s)")

    uplift_models = [n for n in cfg.models.candidates if not build_learner(n).is_baseline]
    ranked = sorted(uplift_models, key=lambda n: m.qini_auc(y, oof[n], t), reverse=True)
    ens_name = "ensemble:" + "+".join(sorted(ranked[: cfg.models.ensemble_top_k]))
    oof[ens_name] = np.mean([oof[n] for n in ranked[: cfg.models.ensemble_top_k]], axis=0)
    seconds[ens_name] = sum(seconds[n] for n in ranked[: cfg.models.ensemble_top_k])
    log(f"  {ens_name:22s} OOF Qini {m.qini_auc(y, oof[ens_name], t):+.4f}")

    rows, curves = [], []
    for name, score in oof.items():
        rep = _ranking_report(y, score, t, exp_cfg.n_bootstrap, seed)
        rows.append(
            {
                "model": name,
                "is_baseline": build_learner(name).is_baseline,
                "fit_seconds": seconds[name],
                **rep,
            }
        )
        curves.append(_curve_points(y, score, t, name, "dev_oof"))
    board = pd.DataFrame(rows).sort_values("qini_auc", ascending=False).reset_index(drop=True)

    # Selection rule (fixed before test is seen): highest out-of-fold Qini wins.
    champion = board.loc[~board.is_baseline, "model"].iloc[0]
    board["is_champion"] = board.model == champion
    for name in board.model:
        diff, p_better = m.paired_bootstrap(
            m.qini_auc, y, oof[champion], oof[name], t, exp_cfg.n_bootstrap, seed
        )
        board.loc[
            board.model == name, ["diff_vs_champion", "diff_low", "diff_high", "p_champion_better"]
        ] = [diff.estimate, diff.low, diff.high, p_better]
    board["noise_floor_low"], board["noise_floor_high"] = m.random_noise_floor(y, t, 200, seed)
    board["rank"] = np.arange(1, len(board) + 1)

    # Targeting depth decided on dev OOF, then checked once on test by `evaluate`.
    margin = margin_per_purchase(cfg.economics, con)
    curve = profit_curve(y, oof[champion], t, margin, cfg.economics.sms_cost, seed=seed)
    depth = best_depth(curve)

    write_table(con, board, "ml.leaderboard")
    write_table(con, pd.concat(curves), "ml.qini_curves")
    write_table(
        con,
        m.decile_table(y, oof[champion], t).assign(model=champion, split="dev_oof"),
        "ml.decile_calibration",
    )
    pd.DataFrame(
        {"client_id": dev.index, "treatment": t, "target": y, "score": oof[champion]}
    ).to_parquet(artifact_path(cfg, "dev_oof_scores.parquet"))
    joblib.dump(
        {"champion": champion, "depth": depth, "margin": margin},
        artifact_path(cfg, "selection.joblib"),
    )
    log_run(
        con,
        cfg,
        "train",
        {"champion": champion, "depth": depth, "oof_qini": float(board.qini_auc.iloc[0])},
    )
    log(f"champion: {champion}  |  dev-chosen depth: {depth:.0%}  |  margin {margin:.1f}")
    return board


def evaluate(cfg: Config, con: duckdb.DuckDBPyConnection, log=print) -> pd.DataFrame:
    """Score the locked test split with models fitted on dev. Every call is logged."""
    seed, n_boot = cfg.experiment.seed, cfg.experiment.n_bootstrap
    sel = joblib.load(artifact_path(cfg, "selection.joblib"))
    champion, depth, margin = sel["champion"], sel["depth"], sel["margin"]
    experiment, split = _labelled(con, cfg)
    dev, test = experiment[split == "dev"], experiment[split == "test"]
    X_dev, X_test = load_features(con, dev.index), load_features(con, test.index)
    params = cfg.models.lightgbm

    con.execute("CREATE SCHEMA IF NOT EXISTS meta")
    con.execute(
        "CREATE TABLE IF NOT EXISTS meta.test_access_log (accessed_at TIMESTAMP, "
        "champion VARCHAR, git_sha VARCHAR)"
    )
    n_prior = con.execute("SELECT count(*) FROM meta.test_access_log").fetchone()[0]
    con.execute(
        "INSERT INTO meta.test_access_log VALUES (?, ?, ?)",
        [datetime.now(UTC), champion, _git_sha()],
    )
    if n_prior:
        log(
            f"[warn] the test split has been scored {n_prior} time(s) before; see "
            "meta.test_access_log. Do not change the model in response to these numbers."
        )

    t, y = test.treatment.to_numpy(), test.target.to_numpy()
    scores = {}
    for name in (champion, "response", "random"):
        model = build_learner(name, params, seed).fit(
            X_dev, dev.treatment.to_numpy(), dev.target.to_numpy()
        )
        scores[name] = model.predict(X_test)
        if name == champion:
            joblib.dump(model, artifact_path(cfg, "model_dev.joblib"))

    rows, curves = [], []
    for name, score in scores.items():
        rep = _ranking_report(y, score, t, n_boot, seed)
        diff, p_better = (
            m.paired_bootstrap(m.qini_auc, y, scores[champion], score, t, n_boot, seed)
            if name != champion
            else (None, None)
        )
        rows.append(
            {
                "model": name,
                "role": "champion" if name == champion else "reference",
                **rep,
                "diff_vs_champion": None if diff is None else diff.estimate,
                "diff_low": None if diff is None else diff.low,
                "diff_high": None if diff is None else diff.high,
                "p_champion_better": p_better,
                "profit_per_1000_at_depth": profit_at(
                    y, score, t, depth, margin, cfg.economics.sms_cost
                ),
            }
        )
        curves.append(_curve_points(y, score, t, name, "test"))
    results = pd.DataFrame(rows)
    results["chosen_depth"] = depth
    results["profit_text_everyone"] = profit_at(
        y, scores["random"], t, 1.0, margin, cfg.economics.sms_cost
    )
    results["ate"] = y[t == 1].mean() - y[t == 0].mean()
    results["n_test"] = len(y)
    results["noise_floor_low"], results["noise_floor_high"] = m.random_noise_floor(y, t, 200, seed)

    write_table(con, results, "ml.test_results")
    old = con.execute("SELECT * FROM ml.qini_curves WHERE split <> 'test'").df()
    write_table(con, pd.concat([old, *curves]), "ml.qini_curves")
    old = con.execute("SELECT * FROM ml.decile_calibration WHERE split <> 'test'").df()
    write_table(
        con,
        pd.concat(
            [old, m.decile_table(y, scores[champion], t).assign(model=champion, split="test")]
        ),
        "ml.decile_calibration",
    )
    log_run(con, cfg, "evaluate", {"test_qini": float(results.qini_auc.iloc[0])})
    return results


def deploy(cfg: Config, con: duckdb.DuckDBPyConnection, log=print) -> pd.DataFrame:
    """Production model: cross-fit on all labelled clients for calibration and policy, refit
    on all of them, then score every card holder and write the send list."""
    seed, econ = cfg.experiment.seed, cfg.economics
    champion = joblib.load(artifact_path(cfg, "selection.joblib"))["champion"]
    params = cfg.models.lightgbm
    experiment = load_experiment(con)
    X_all = load_features(con)
    X_lab = X_all.loc[experiment.index]
    t, y = experiment.treatment.to_numpy(), experiment.target.to_numpy()
    folds = cv_folds(t, y, cfg.experiment.n_folds, seed)

    log("  cross-fitting champion on all labelled clients")
    oof = _crossfit(champion, X_lab, t, y, folds, params, seed)
    calibrator = UpliftCalibrator().fit(oof, t, y)
    margin = margin_per_purchase(econ, con)
    curve = profit_curve(y, oof, t, margin, econ.sms_cost, seed=seed)
    break_even = econ.sms_cost / margin

    log("  refitting on all labelled clients")
    model = build_learner(champion, params, seed).fit(X_lab, t, y)
    # Baseline purchase probability without the SMS, for customer segmentation.
    control = lgb.LGBMClassifier(**params, random_state=seed).fit(X_lab[t == 0], y[t == 0])

    log("  scoring all clients")
    raw = model.predict(X_all)
    uplift = calibrator.transform(raw)
    p_control = control.predict_proba(X_all)[:, 1]
    scores = pd.DataFrame(
        {
            "client_id": X_all.index,
            "uplift_score": raw,
            "expected_uplift": uplift,
            "p_buy_without_sms": p_control,
        }
    )
    scores["p_buy_with_sms"] = np.clip(scores.p_buy_without_sms + scores.expected_uplift, 0, 1)
    scores["rank"] = scores.uplift_score.rank(method="first", ascending=False).astype(int)
    scores["decile"] = np.ceil(scores["rank"] / len(scores) * 10).astype(int)
    scores["expected_profit"] = scores.expected_uplift * margin - econ.sms_cost
    scores["segment"] = np.select(
        [
            scores.expected_uplift <= 0,
            scores.expected_uplift >= break_even,
            scores.p_buy_without_sms >= 0.7,
        ],
        ["Do not disturb", "Persuadable", "Sure thing"],
        default="Lost cause",
    )
    selected = scores.expected_uplift > break_even
    rng = np.random.default_rng(seed)
    holdout = selected & (rng.random(len(scores)) < econ.holdout_share)
    scores["action"] = np.where(holdout, "holdout", np.where(selected, "send", "skip"))
    scores["in_experiment"] = scores.client_id.isin(experiment.index)
    # Experiment clients were used to fit the production model, so judging its ranking on them
    # needs scores from fold models that never saw them: out-of-fold decile among the labelled.
    oof_rank = pd.Series(oof, index=experiment.index).rank(method="first", ascending=False)
    oof_decile = np.ceil(oof_rank / len(oof_rank) * 10).astype(int)
    scores["oof_decile"] = scores.client_id.map(oof_decile).astype("Int64")
    scores["scored_at"] = datetime.now(UTC)

    write_table(con, scores, "ml.scores")
    write_table(con, curve.assign(model=champion), "ml.profit_curves")
    write_table(
        con,
        pd.DataFrame(
            [
                {
                    "champion": champion,
                    "currency": econ.currency,
                    "sms_cost": econ.sms_cost,
                    "margin_per_purchase": margin,
                    "break_even_uplift": break_even,
                    "best_depth": best_depth(curve),
                    "holdout_share": econ.holdout_share,
                    "n_scored": len(scores),
                    "n_send": int((scores.action == "send").sum()),
                    "n_holdout": int(holdout.sum()),
                    "ate": y[t == 1].mean() - y[t == 0].mean(),
                    "expected_incremental_purchases": float(
                        scores.loc[scores.action == "send", "expected_uplift"].sum()
                    ),
                    "expected_profit": float(
                        scores.loc[scores.action == "send", "expected_profit"].sum()
                    ),
                    "blanket_expected_profit": float(
                        len(scores)
                        * ((y[t == 1].mean() - y[t == 0].mean()) * margin - econ.sms_cost)
                    ),
                }
            ]
        ),
        "ml.policy",
    )
    write_table(con, sensitivity(curve, margin, BREAK_EVENS), "ml.sensitivity")
    write_table(
        con,
        calibrator.bins_.reset_index().assign(
            calibrated=calibrator.transform(calibrator.bins_.score)
        ),
        "ml.calibration_bins",
    )

    unlabelled = X_all.loc[~X_all.index.isin(experiment.index)]
    write_table(con, psi_table(X_lab, unlabelled, cfg.monitoring), "ml.drift")
    joblib.dump(
        {
            "model": model,
            "calibrator": calibrator,
            "control_model": control,
            "champion": champion,
            "features": list(X_all.columns),
            "margin": margin,
            "sms_cost": econ.sms_cost,
            "trained_at": datetime.now(UTC).isoformat(),
            "git_sha": _git_sha(),
        },
        artifact_path(cfg, "model_prod.joblib"),
    )
    log_run(con, cfg, "deploy", {"n_send": int((scores.action == "send").sum())})
    return scores


def explain(
    cfg: Config, con: duckdb.DuckDBPyConnection, n_rows: int = 50_000, log=print
) -> pd.DataFrame:
    """Global drivers of the deployed uplift scores via a surrogate model + exact TreeSHAP.

    The champion may be an ensemble, so a LightGBM regressor is fitted to reproduce its scores
    and LightGBM's `pred_contrib` splits each surrogate prediction among the features. The
    surrogate's R^2 says how faithful the explanation is.
    """
    seed = cfg.experiment.seed
    scores = con.execute("SELECT client_id, uplift_score FROM ml.scores").df()
    sample = scores.sample(min(n_rows, len(scores)), random_state=seed)
    X = load_features(con).loc[sample.client_id]
    target = sample.uplift_score.to_numpy()
    surrogate = lgb.LGBMRegressor(
        n_estimators=400, learning_rate=0.05, num_leaves=63, verbose=-1, random_state=seed
    ).fit(X, target)
    pred = surrogate.predict(X)
    r2 = 1 - np.sum((target - pred) ** 2) / np.sum((target - target.mean()) ** 2)
    contrib = surrogate.predict(X, pred_contrib=True)[:, :-1]
    imp = np.abs(contrib).mean(axis=0)
    rows = []
    for j, col in enumerate(X.columns):
        values = X[col].cat.codes if hasattr(X[col], "cat") else X[col]
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
    importance["surrogate_r2"] = r2
    write_table(con, importance, "ml.feature_importance")

    dep = []
    for col in importance.feature.head(8):
        if hasattr(X[col], "cat"):
            continue
        j = list(X.columns).index(col)
        frame = pd.DataFrame({"value": X[col].to_numpy(), "contribution": contrib[:, j]}).dropna()
        frame["bin"] = pd.qcut(frame.value.rank(method="first"), 20, labels=False)
        agg = (
            frame.groupby("bin")
            .agg(value=("value", "median"), contribution=("contribution", "mean"))
            .reset_index()
        )
        dep.append(agg.assign(feature=col))
    write_table(con, pd.concat(dep), "ml.feature_dependence")
    log(f"surrogate R^2 = {r2:.3f}; top drivers: {', '.join(importance.feature.head(5))}")
    log_run(con, cfg, "explain", {"surrogate_r2": r2})
    return importance
