"""Campaign economics and policy value.

Policy value per 1,000 customers vs texting nobody, estimated by the difference in means inside
the selected group: 1000 * share * (margin * uplift - sms_cost).
"""

from __future__ import annotations

from collections.abc import Mapping

import duckdb
import numpy as np
import pandas as pd

from uplift_engine.config import Economics
from uplift_engine.evaluation.metrics import (
    ArmBootstrap,
    MetricInputError,
    UndefinedMetricError,
    _uplift_by_depth,
    validate,
)

DEPTHS = np.round(np.arange(0.01, 1.0001, 0.01), 2)


def margin_per_purchase(econ: Economics, con: duckdb.DuckDBPyConnection | None = None) -> float:
    """Configured margin, or mean basket x gross margin."""
    if econ.margin_per_purchase is not None:
        margin = econ.margin_per_purchase
    else:
        if con is None:
            raise ValueError("margin_per_purchase is null: pass a warehouse connection")
        basket = con.execute("SELECT avg(purchase_sum) FROM staging.stg_transactions").fetchone()[0]
        margin = float(basket) * econ.gross_margin
    if not margin > 0:
        raise ValueError(f"margin per purchase must be positive, got {margin}")
    return float(margin)


def break_even(margin: float, sms_cost: float) -> float:
    return sms_cost / margin


def select_by_effect(effect: np.ndarray, margin: float, sms_cost: float) -> np.ndarray:
    """Text when calibrated effect x margin is strictly above the SMS cost."""
    return np.asarray(effect, dtype=float) * margin > sms_cost


def select_top(score: np.ndarray, share: float) -> np.ndarray:
    if not 0 <= share <= 1:
        raise MetricInputError(f"share must be in [0, 1], got {share}")
    n = int(np.floor(share * len(score)))
    flags = np.zeros(len(score), dtype=bool)
    flags[np.argsort(-np.asarray(score, dtype=float), kind="mergesort")[:n]] = True
    return flags


def _value(y, t, sel, margin, cost) -> float:
    n = len(y)
    if not sel.any():
        return 0.0
    yt, ys = t[sel], y[sel]
    if yt.min() == yt.max():
        raise UndefinedMetricError("selected group lacks treated or control customers")
    lift = ys[yt == 1].mean() - ys[yt == 0].mean()
    return float(1000 * sel.sum() / n * (margin * lift - cost))


def policy_value(y, t, selected, margin: float, sms_cost: float) -> float:
    y, _, t = validate(y, np.zeros(len(y)), t)
    sel = np.asarray(selected, dtype=bool)
    if len(sel) != len(y):
        raise MetricInputError("selected must align with y")
    return _value(y, t, sel, margin, sms_cost)


def expected_random_value(y, t, share: float, margin: float, sms_cost: float) -> float:
    y, _, t = validate(y, np.zeros(len(y)), t)
    return share * _value(y, t, np.ones(len(y), dtype=bool), margin, sms_cost)


def evaluate_policies(
    y,
    t,
    policies: Mapping[str, np.ndarray],
    margin: float,
    sms_cost: float,
    draws: ArmBootstrap,
    references: tuple[str, ...] = ("blanket", "no_send"),
    random_shares: Mapping[str, float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Value of each policy and paired differences against reference policies."""
    y, _, t = validate(y, np.zeros(len(y)), t)
    pol = {"no_send": np.zeros(len(y), bool), "blanket": np.ones(len(y), bool)}
    pol |= {k: np.asarray(v, dtype=bool) for k, v in policies.items()}
    for k, v in pol.items():
        if len(v) != len(y):
            raise MetricInputError(f"policy {k} does not align with the outcomes")
    rand = dict(random_shares or {})

    def all_values(idx):
        yy, tt = y[idx], t[idx]
        out = {}
        for k, v in pol.items():
            try:
                out[k] = _value(yy, tt, v[idx], margin, sms_cost)
            except UndefinedMetricError:
                out[k] = np.nan
        for k, share in rand.items():
            out[k] = share * out["blanket"]
        return out

    point = all_values(np.arange(len(y)))
    reps = [all_values(idx) for idx in draws.indices]
    names = list(point)
    rep_mat = {k: np.array([r[k] for r in reps]) for k in names}

    rows = []
    for k in names:
        ci = draws.interval(point[k], rep_mat[k])
        sel = pol.get(k)
        rows.append(
            {
                "policy": k,
                "value_per_1000": point[k],
                "value_low": ci.low,
                "value_high": ci.high,
                "n_undefined": ci.n_undefined,
                "selected_share": float(sel.mean()) if sel is not None else rand[k],
                "n_selected_treated": int((sel & (t == 1)).sum()) if sel is not None else None,
                "n_selected_control": int((sel & (t == 0)).sum()) if sel is not None else None,
                "kind": "expected random (share x blanket)" if k in rand else "fixed selection",
            }
        )
    diffs = []
    for k in names:
        for ref in references:
            if ref == k:
                continue
            d = rep_mat[k] - rep_mat[ref]
            ci = draws.interval(point[k] - point[ref], d)
            ok = d[~np.isnan(d)]
            diffs.append(
                {
                    "policy": k,
                    "reference": ref,
                    "difference_per_1000": point[k] - point[ref],
                    "difference_low": ci.low,
                    "difference_high": ci.high,
                    "frac_positive": float((ok > 0).mean()) if len(ok) else np.nan,
                    "n_undefined": ci.n_undefined,
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(diffs)


def profit_curve(
    y, score, t, margin: float, sms_cost: float, draws: ArmBootstrap, depths: np.ndarray = DEPTHS
) -> pd.DataFrame:
    """Uplift and value per 1,000 at every depth, with pointwise bootstrap bands."""
    y, score, t = validate(y, score, t)
    point = _uplift_by_depth(y, score, t, depths)
    boots = np.array([_uplift_by_depth(y[i], score[i], t[i], depths) for i in draws.indices])

    def to_value(u):
        return 1000 * depths * (margin * u - sms_cost)

    q = lambda a, p: np.nanquantile(a, p, axis=0)  # noqa: E731
    lo, hi = draws.alpha / 2, 1 - draws.alpha / 2
    vb = to_value(boots)
    return pd.DataFrame(
        {
            "depth": depths,
            "uplift": point,
            "uplift_low": q(boots, lo),
            "uplift_high": q(boots, hi),
            "additional_buyers_per_1000": 1000 * depths * point,
            "value_per_1000": to_value(point),
            "value_low": q(vb, lo),
            "value_high": q(vb, hi),
            "n_undefined": np.isnan(boots).sum(axis=0),
        }
    )


def best_depth(curve: pd.DataFrame) -> float:
    """Depth with the highest estimated value, or 0 if none is positive."""
    vals = curve.value_per_1000.to_numpy()
    if np.all(np.isnan(vals)):
        return 0.0
    i = int(np.nanargmax(vals))
    return float(curve.depth.iloc[i]) if vals[i] > 0 else 0.0


def sensitivity(curve: pd.DataFrame, margin: float, break_evens) -> pd.DataFrame:
    rows = []
    for be in break_evens:
        value = 1000 * curve.depth * (margin * curve.uplift - be * margin)
        i = int(np.nanargmax(value.to_numpy()))
        best = value.iloc[i] > 0
        rows.append(
            {
                "break_even_uplift": be,
                "best_depth": curve.depth.iloc[i] if best else 0.0,
                "value_per_1000": value.iloc[i] if best else 0.0,
            }
        )
    return pd.DataFrame(rows)
