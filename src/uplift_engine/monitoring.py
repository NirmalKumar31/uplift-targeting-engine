"""Experiment validity (covariate balance) and population drift (PSI)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from uplift_engine.config import Monitoring


def _numeric(X: pd.DataFrame) -> pd.DataFrame:
    return X.apply(lambda s: s.cat.codes.replace(-1, np.nan) if hasattr(s, "cat") else s)


def balance_table(X: pd.DataFrame, treatment: pd.Series) -> pd.DataFrame:
    """Standardised mean difference per feature, SMS vs no-SMS arm.

    |SMD| < 0.1 is the usual bar for a successfully randomised experiment.
    """
    Xn = _numeric(X)
    t = treatment.reindex(Xn.index).to_numpy() == 1
    rows = []
    for col in Xn.columns:
        a, b = Xn.loc[t, col].dropna(), Xn.loc[~t, col].dropna()
        pooled = np.sqrt((a.var() + b.var()) / 2)
        smd = (a.mean() - b.mean()) / pooled if pooled > 0 else 0.0
        rows.append(
            {
                "feature": col,
                "mean_treated": a.mean(),
                "mean_control": b.mean(),
                "smd": smd,
                "balanced": abs(smd) < 0.1,
            }
        )
    return pd.DataFrame(rows)


def psi(expected: pd.Series, actual: pd.Series, n_bins: int = 10) -> float:
    """Population stability index over reference-decile bins (plus a missing-value bin)."""
    expected, actual = expected.astype(float), actual.astype(float)
    ref = expected.dropna()
    if ref.nunique() <= n_bins:
        edges = np.unique(ref)
        bins = lambda s: pd.Categorical(s, categories=edges).codes  # noqa: E731
    else:
        edges = np.unique(np.quantile(ref, np.linspace(0, 1, n_bins + 1))[1:-1])
        bins = lambda s: np.searchsorted(edges, s, side="right")  # noqa: E731

    e_counts = pd.Series(np.where(expected.isna(), -2, bins(expected.fillna(0)))).value_counts()
    a_counts = pd.Series(np.where(actual.isna(), -2, bins(actual.fillna(0)))).value_counts()
    keys = sorted(set(e_counts.index) | set(a_counts.index))
    e = np.array([e_counts.get(k, 0) for k in keys], float) / len(expected)
    a = np.array([a_counts.get(k, 0) for k in keys], float) / len(actual)
    e, a = np.clip(e, 1e-4, None), np.clip(a, 1e-4, None)
    return float(np.sum((a - e) * np.log(a / e)))


def psi_table(reference: pd.DataFrame, current: pd.DataFrame, cfg: Monitoring) -> pd.DataFrame:
    ref, cur = _numeric(reference), _numeric(current)
    rows = [{"feature": c, "psi": psi(ref[c], cur[c])} for c in ref.columns]
    out = pd.DataFrame(rows)
    out["status"] = np.select(
        [out.psi >= cfg.psi_fail, out.psi >= cfg.psi_warn], ["fail", "warn"], "ok"
    )
    return out.sort_values("psi", ascending=False)
