"""Uplift ranking metrics.

Definitions match scikit-uplift (`qini_auc_score(negative_effect=True)`, `uplift_at_k(strategy=
"overall")`) so numbers are comparable with the literature, but are implemented here directly:
sklift pins an old scikit-learn, and these vectorised versions are fast enough to bootstrap.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def _as_arrays(y, score, t):
    y, score, t = (np.asarray(a) for a in (y, score, t))
    if not (len(y) == len(score) == len(t)):
        raise ValueError("y, score and t must have the same length")
    return y.astype(float), score.astype(float), t.astype(float)


def qini_curve(y, score, t) -> tuple[np.ndarray, np.ndarray]:
    """Qini curve: incremental responders among the top n, at every distinct score threshold."""
    y, score, t = _as_arrays(y, score, t)
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
    """Normalised area between the Qini curve and the random line (1 = perfect, 0 = random)."""
    y, score, t = _as_arrays(y, score, t)
    x, q = qini_curve(y, score, t)
    xp, qp = qini_curve(y, y * t - y * (1 - t), t)
    baseline = xp[-1] * qp[-1] / 2
    return (np.trapezoid(q, x) - baseline) / (np.trapezoid(qp, xp) - baseline)


def uplift_at_k(y, score, t, k: float) -> float:
    """Treated minus control response rate among the top k share of clients by score."""
    y, score, t = _as_arrays(y, score, t)
    n = int(len(y) * k)
    top = np.argsort(score, kind="mergesort")[::-1][:n]
    yt, tt = y[top], t[top]
    return yt[tt == 1].mean() - yt[tt == 0].mean()


def uplift_by_depth(y, score, t, depths: np.ndarray) -> np.ndarray:
    """uplift_at_k for many depths in one sort (cumulative, so O(n log n) total)."""
    y, score, t = _as_arrays(y, score, t)
    order = np.argsort(score, kind="mergesort")[::-1]
    y, t = y[order], t[order]
    n_t, n_c = np.cumsum(t), np.cumsum(1 - t)
    y_t, y_c = np.cumsum(y * t), np.cumsum(y * (1 - t))
    pos = np.clip((depths * len(y)).astype(int) - 1, 0, len(y) - 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return y_t[pos] / n_t[pos] - y_c[pos] / n_c[pos]


def decile_table(y, score, t, pred: np.ndarray | None = None, n_bins: int = 10) -> pd.DataFrame:
    """Observed (and optionally predicted) uplift per score decile, 1 = highest scores."""
    y, score, t = _as_arrays(y, score, t)
    rank = pd.Series(score).rank(method="first", ascending=False).to_numpy()
    decile = np.ceil(rank / len(score) * n_bins).astype(int)
    df = pd.DataFrame({"decile": decile, "y": y, "t": t, "pred": score if pred is None else pred})
    rows = []
    for d, g in df.groupby("decile"):
        rt, rc = g.loc[g.t == 1, "y"].mean(), g.loc[g.t == 0, "y"].mean()
        n1, n0 = (g.t == 1).sum(), (g.t == 0).sum()
        se = np.sqrt(rt * (1 - rt) / max(n1, 1) + rc * (1 - rc) / max(n0, 1))
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

    def as_dict(self, prefix: str) -> dict[str, float]:
        return {prefix: self.estimate, f"{prefix}_low": self.low, f"{prefix}_high": self.high}


def _stratified_resample(t: np.ndarray, y: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Resample within each treatment x outcome cell so every replicate keeps the arm balance."""
    idx = []
    for cell in np.unique(t * 2 + y):
        members = np.flatnonzero(t * 2 + y == cell)
        idx.append(rng.choice(members, size=members.size, replace=True))
    # Shuffle: concatenated cells would otherwise break score ties by arm in uplift_at_k.
    return rng.permutation(np.concatenate(idx))


def bootstrap(metric, y, score, t, n_boot: int = 500, seed: int = 0, alpha: float = 0.05):
    """Percentile CI for metric(y, score, t)."""
    y, score, t = _as_arrays(y, score, t)
    rng = np.random.default_rng(seed)
    stats = [
        metric(y[i], score[i], t[i])
        for i in (_stratified_resample(t, y, rng) for _ in range(n_boot))
    ]
    lo, hi = np.nanquantile(stats, [alpha / 2, 1 - alpha / 2])
    return Interval(metric(y, score, t), float(lo), float(hi))


def paired_bootstrap(metric, y, score_a, score_b, t, n_boot: int = 500, seed: int = 0):
    """CI for metric(a) - metric(b) on shared resamples, and P(a > b)."""
    y, score_a, t = _as_arrays(y, score_a, t)
    score_b = np.asarray(score_b, dtype=float)
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(n_boot):
        i = _stratified_resample(t, y, rng)
        diffs.append(metric(y[i], score_a[i], t[i]) - metric(y[i], score_b[i], t[i]))
    diffs = np.asarray(diffs)
    point = metric(y, score_a, t) - metric(y, score_b, t)
    lo, hi = np.quantile(diffs, [0.025, 0.975])
    return Interval(point, float(lo), float(hi)), float((diffs > 0).mean())


def random_noise_floor(y, t, n_rankings: int = 200, seed: int = 0) -> tuple[float, float]:
    """95% band of Qini AUC for random rankings: scores inside it are indistinguishable
    from luck."""
    y, _, t = _as_arrays(y, y, t)
    rng = np.random.default_rng(seed)
    vals = [qini_auc(y, rng.random(len(y)), t) for _ in range(n_rankings)]
    lo, hi = np.quantile(vals, [0.025, 0.975])
    return float(lo), float(hi)
