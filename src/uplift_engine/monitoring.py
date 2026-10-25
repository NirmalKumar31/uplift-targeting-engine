"""Covariate balance between experiment arms, and a historical population comparison (PSI).

Balance. The SMS was assigned at random according to the dataset documentation; that is an
assumption about the design, not something these numbers prove. What is measured: for numeric
features, the standardised mean difference (SMD) on non-missing values; for categorical features,
the SMD of each level's indicator; for every feature, the SMD of its missingness indicator.
|SMD| < 0.1 is the conventional balance threshold.

PSI. Compares experiment customers (training population) with the non-experiment customers of the
*same historical snapshot*. It is a diagnostic of population similarity, not temporal
monitoring of a new campaign and not a deployment gate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from uplift_engine.config import Monitoring

BALANCE_THRESHOLD = 0.1


def _smd(a: pd.Series, b: pd.Series) -> float:
    a, b = a.astype(float), b.astype(float)
    pooled = np.sqrt((a.var() + b.var()) / 2)
    return float((a.mean() - b.mean()) / pooled) if pooled > 0 else 0.0


def balance_table(X: pd.DataFrame, treatment: pd.Series) -> pd.DataFrame:
    t = treatment.reindex(X.index).to_numpy() == 1
    rows = []
    for col in X.columns:
        s = X[col]
        miss = s.isna()
        if miss.any():
            rows.append(
                {
                    "feature": col,
                    "check": "missingness",
                    "level": None,
                    "treated": miss[t].mean(),
                    "control": miss[~t].mean(),
                    "smd": _smd(miss[t], miss[~t]),
                }
            )
        if hasattr(s, "cat"):
            for level in s.cat.categories:
                ind = (s == level).astype(float)
                rows.append(
                    {
                        "feature": col,
                        "check": "category share",
                        "level": str(level),
                        "treated": ind[t].mean(),
                        "control": ind[~t].mean(),
                        "smd": _smd(ind[t], ind[~t]),
                    }
                )
        else:
            a, b = s[t & ~miss], s[~t & ~miss]
            rows.append(
                {
                    "feature": col,
                    "check": "mean (non-missing)",
                    "level": None,
                    "treated": a.mean(),
                    "control": b.mean(),
                    "smd": _smd(a, b),
                }
            )
    out = pd.DataFrame(rows)
    out["balanced"] = out.smd.abs() < BALANCE_THRESHOLD
    return out


def _numeric(X: pd.DataFrame) -> pd.DataFrame:
    return X.apply(lambda s: s.cat.codes.replace(-1, np.nan) if hasattr(s, "cat") else s)


def psi(expected: pd.Series, actual: pd.Series, n_bins: int = 10) -> float:
    """Population stability index over reference-decile bins plus a missing-value bin."""
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
    out = pd.DataFrame([{"feature": c, "psi": psi(ref[c], cur[c])} for c in ref.columns])
    out["status"] = np.select(
        [out.psi >= cfg.psi_fail, out.psi >= cfg.psi_warn], ["high", "moderate"], "low"
    )
    out["comparison"] = "experiment vs non-experiment customers, same historical snapshot"
    out["role"] = "diagnostic (not a deployment gate)"
    return out.sort_values("psi", ascending=False)
