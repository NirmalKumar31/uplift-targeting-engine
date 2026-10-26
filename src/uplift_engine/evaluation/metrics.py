"""Uplift metrics and the customer-within-arm bootstrap.

Definitions match scikit-uplift (qini_auc_score with negative_effect=True, uplift_at_k with
strategy="overall"). Intervals cover sampling of evaluation customers for fixed scores only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


class MetricInputError(ValueError):
    """Inputs violate the metric contract."""


class UndefinedMetricError(ValueError):
    """Metric is undefined for these inputs."""


def validate(y, score, t, *, need_both_arms: bool = True):
    y, score, t = (np.asarray(a) for a in (y, score, t))
    if not (y.ndim == score.ndim == t.ndim == 1):
        raise MetricInputError("y, score and t must be one-dimensional")
    if not (len(y) == len(score) == len(t)):
        raise MetricInputError(
            f"y, score and t must have the same length (got {len(y)}, {len(score)}, {len(t)})"
        )
    if len(y) == 0:
        raise MetricInputError("inputs are empty")
    for name, arr in (("y", y), ("t", t)):
        if not np.isin(arr, (0, 1)).all():
            raise MetricInputError(f"{name} must be binary 0/1")
    score = score.astype(float)
    if not np.isfinite(score).all():
        raise MetricInputError("score contains NaN or infinite values")
    t = t.astype(float)
    if need_both_arms and (t.sum() == 0 or t.sum() == len(t)):
        raise MetricInputError("both treatment arms must be present")
    return y.astype(float), score, t


def _check_fraction(k: float, name: str = "k") -> None:
    if not (0 < k <= 1):
        raise MetricInputError(f"{name} must be in (0, 1], got {k}")


def qini_curve(y, score, t) -> tuple[np.ndarray, np.ndarray]:
    y, score, t = validate(y, score, t)
    return _qini_curve(y, score, t)


def _qini_curve(y, score, t):
    order = np.argsort(score, kind="mergesort")[::-1]
    y, score, t = y[order], score[order], t[order]
    idx = np.r_[np.where(np.diff(score))[0], score.size - 1]
    n_t = np.cumsum(t)[idx]
    n_all = idx + 1.0
    n_c = n_all - n_t
    y_t = np.cumsum(y * t)[idx]
    y_c = np.cumsum(y * (1 - t))[idx]
    ratio = np.divide(n_t, n_c, out=np.zeros_like(n_t), where=n_c != 0)
    return np.r_[0.0, n_all], np.r_[0.0, y_t - y_c * ratio]


def qini_auc(y, score, t) -> float:
    """Normalised Qini AUC: 0 = random ranking, 1 = perfect."""
    y, score, t = validate(y, score, t)
    return _qini_auc(y, score, t)


def _qini_auc(y, score, t) -> float:
    x, q = _qini_curve(y, score, t)
    xp, qp = _qini_curve(y, y * t - y * (1 - t), t)
    baseline = xp[-1] * qp[-1] / 2
    denom = np.trapezoid(qp, xp) - baseline
    if np.isclose(denom, 0.0):
        raise UndefinedMetricError("perfect Qini area equals the random baseline")
    return float((np.trapezoid(q, x) - baseline) / denom)


def uplift_at_k(y, score, t, k: float) -> float:
    """Treated minus control response rate in the top k share by score."""
    y, score, t = validate(y, score, t)
    _check_fraction(k)
    out = _uplift_by_depth(y, score, t, np.array([k]))[0]
    if np.isnan(out):
        raise UndefinedMetricError(f"top {k:.0%} lacks treated or control customers")
    return float(out)


def uplift_by_depth(y, score, t, depths) -> np.ndarray:
    """uplift_at_k for several depths at once; NaN where a slice lacks an arm."""
    y, score, t = validate(y, score, t)
    depths = np.asarray(depths, dtype=float)
    for d in depths:
        _check_fraction(d, "depth")
    return _uplift_by_depth(y, score, t, depths)


def _uplift_by_depth(y, score, t, depths):
    order = np.argsort(score, kind="mergesort")[::-1]
    y, t = y[order], t[order]
    n_t, n_c = np.cumsum(t), np.cumsum(1 - t)
    y_t, y_c = np.cumsum(y * t), np.cumsum(y * (1 - t))
    pos = np.clip(np.floor(depths * len(y)).astype(int) - 1, 0, len(y) - 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        rt = np.where(n_t[pos] > 0, y_t[pos] / np.maximum(n_t[pos], 1), np.nan)
        rc = np.where(n_c[pos] > 0, y_c[pos] / np.maximum(n_c[pos], 1), np.nan)
    return rt - rc


def ate(y, t) -> float:
    y, _, t = validate(y, np.zeros(len(y)), t)
    return float(y[t == 1].mean() - y[t == 0].mean())


def decile_table(y, score, t, pred: np.ndarray | None = None, n_bins: int = 10) -> pd.DataFrame:
    """Observed uplift per score decile (1 = highest scores)."""
    y, score, t = validate(y, score, t)
    rank = pd.Series(score).rank(method="first", ascending=False).to_numpy()
    decile = np.ceil(rank / len(score) * n_bins).astype(int)
    df = pd.DataFrame({"decile": decile, "y": y, "t": t, "pred": score if pred is None else pred})
    rows = []
    for d, g in df.groupby("decile"):
        n1, n0 = int((g.t == 1).sum()), int((g.t == 0).sum())
        rt = g.loc[g.t == 1, "y"].mean() if n1 else np.nan
        rc = g.loc[g.t == 0, "y"].mean() if n0 else np.nan
        se = np.sqrt(rt * (1 - rt) / n1 + rc * (1 - rc) / n0) if n1 and n0 else np.nan
        rows.append(
            dict(
                decile=d,
                n=len(g),
                n_treated=n1,
                n_control=n0,
                rate_treated=rt,
                rate_control=rc,
                observed_uplift=rt - rc,
                observed_se=se,
                predicted_uplift=g.pred.mean(),
            )
        )
    return pd.DataFrame(rows)


@dataclass
class Interval:
    estimate: float
    low: float
    high: float
    n_boot: int = 0
    n_undefined: int = 0

    def as_dict(self, prefix: str) -> dict[str, float]:
        return {prefix: self.estimate, f"{prefix}_low": self.low, f"{prefix}_high": self.high}


@dataclass
class ArmBootstrap:
    """Bootstrap draws that resample customers within each treatment arm."""

    t: np.ndarray
    n_boot: int
    seed: int
    alpha: float = 0.05
    indices: list[np.ndarray] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.n_boot < 1:
            raise MetricInputError("n_boot must be >= 1")
        t = np.asarray(self.t)
        arms = [np.flatnonzero(t == a) for a in (0, 1)]
        if any(len(a) == 0 for a in arms):
            raise MetricInputError("both treatment arms must be present to bootstrap")
        rng = np.random.default_rng(self.seed)
        self.indices = [
            rng.permutation(
                np.concatenate([rng.choice(a, size=len(a), replace=True) for a in arms])
            )
            for _ in range(self.n_boot)
        ]

    @property
    def spec(self) -> dict:
        return {
            "method": "percentile",
            "resampling_unit": "customer",
            "strata": "treatment arm (sizes fixed)",
            "n_boot": self.n_boot,
            "seed": self.seed,
            "confidence": 1 - self.alpha,
            "includes": "evaluation-sample variability for fixed scores only",
        }

    def replicates(self, stat: Callable[[np.ndarray], float | np.ndarray]) -> np.ndarray:
        out = []
        for idx in self.indices:
            try:
                out.append(stat(idx))
            except UndefinedMetricError:
                out.append(np.nan)
        return np.asarray(out, dtype=float)

    def interval(self, estimate: float, reps: np.ndarray) -> Interval:
        reps = np.asarray(reps, dtype=float)
        ok = ~np.isnan(reps)
        if not ok.any():
            return Interval(estimate, np.nan, np.nan, self.n_boot, int((~ok).sum()))
        lo, hi = np.quantile(reps[ok], [self.alpha / 2, 1 - self.alpha / 2])
        return Interval(float(estimate), float(lo), float(hi), self.n_boot, int((~ok).sum()))


def arm_bootstrap(t, n_boot: int, seed: int, alpha: float = 0.05) -> ArmBootstrap:
    return ArmBootstrap(np.asarray(t), n_boot, seed, alpha)


def bootstrap(
    metric,
    y,
    score,
    t,
    n_boot: int = 500,
    seed: int = 0,
    alpha: float = 0.05,
    draws: ArmBootstrap | None = None,
) -> Interval:
    y, score, t = validate(y, score, t)
    draws = draws or arm_bootstrap(t, n_boot, seed, alpha)
    reps = draws.replicates(lambda i: metric(y[i], score[i], t[i]))
    return draws.interval(metric(y, score, t), reps)


@dataclass
class PairedResult:
    difference: Interval
    frac_positive: float
    frac_zero: float


def paired_bootstrap(
    metric,
    y,
    score_a,
    score_b,
    t,
    n_boot: int = 500,
    seed: int = 0,
    alpha: float = 0.05,
    draws: ArmBootstrap | None = None,
) -> PairedResult:
    """Interval for metric(a) - metric(b) on shared draws."""
    y, score_a, t = validate(y, score_a, t)
    _, score_b, _ = validate(y, score_b, t)
    draws = draws or arm_bootstrap(t, n_boot, seed, alpha)
    reps = draws.replicates(
        lambda i: metric(y[i], score_a[i], t[i]) - metric(y[i], score_b[i], t[i])
    )
    point = metric(y, score_a, t) - metric(y, score_b, t)
    ok = reps[~np.isnan(reps)]
    return PairedResult(
        draws.interval(point, reps),
        float((ok > 0).mean()) if len(ok) else np.nan,
        float((ok == 0).mean()) if len(ok) else np.nan,
    )


def random_noise_floor(y, t, n_rankings: int = 200, seed: int = 0) -> tuple[float, float]:
    """95% range of Qini AUC for random rankings."""
    y, _, t = validate(y, np.zeros(len(y)), t)
    rng = np.random.default_rng(seed)
    vals = [_qini_auc(y, rng.random(len(y)), t) for _ in range(n_rankings)]
    lo, hi = np.quantile(vals, [0.025, 0.975])
    return float(lo), float(hi)
