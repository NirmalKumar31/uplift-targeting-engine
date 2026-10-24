"""Campaign economics: turn an uplift ranking into a profit-maximising send decision.

Texting the top share k of clients earns, per client in the base,
    profit(k) = k * (margin * uplift@k - sms_cost)
so only the break-even uplift sms_cost / margin matters for where to stop.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from uplift_engine.config import Economics
from uplift_engine.evaluation.metrics import _as_arrays, _stratified_resample, uplift_by_depth

DEPTHS = np.round(np.arange(0.01, 1.0001, 0.01), 2)


def margin_per_purchase(econ: Economics, con: duckdb.DuckDBPyConnection | None = None) -> float:
    """Profit from one incremental purchase: configured, or mean basket x gross margin."""
    if econ.margin_per_purchase is not None:
        return econ.margin_per_purchase
    if con is None:
        raise ValueError("margin_per_purchase is null: pass a warehouse connection to derive it")
    basket = con.execute("SELECT avg(purchase_sum) FROM staging.stg_transactions").fetchone()[0]
    return float(basket) * econ.gross_margin


def profit_curve(
    y,
    score,
    t,
    margin: float,
    sms_cost: float,
    depths: np.ndarray = DEPTHS,
    n_boot: int = 200,
    seed: int = 0,
) -> pd.DataFrame:
    """Uplift and profit per 1,000 clients at every targeting depth, with bootstrap CIs."""
    y, score, t = _as_arrays(y, score, t)
    point = uplift_by_depth(y, score, t, depths)
    rng = np.random.default_rng(seed)
    boots = np.array(
        [
            uplift_by_depth(y[i], score[i], t[i], depths)
            for i in (_stratified_resample(t, y, rng) for _ in range(n_boot))
        ]
    )
    to_profit = lambda u: 1000 * depths * (margin * u - sms_cost)  # noqa: E731
    lo, hi = np.nanquantile(boots, [0.025, 0.975], axis=0)
    p_boot = to_profit(boots)
    return pd.DataFrame(
        {
            "depth": depths,
            "uplift": point,
            "uplift_low": lo,
            "uplift_high": hi,
            "incremental_per_1000": 1000 * depths * point,
            "profit_per_1000": to_profit(point),
            "profit_low": np.nanquantile(p_boot, 0.025, axis=0),
            "profit_high": np.nanquantile(p_boot, 0.975, axis=0),
        }
    )


def best_depth(curve: pd.DataFrame) -> float:
    """Depth with the highest expected profit; 0 (text nobody) when nothing is profitable."""
    row = curve.loc[curve.profit_per_1000.idxmax()]
    return float(row.depth) if row.profit_per_1000 > 0 else 0.0


def profit_at(y, score, t, depth: float, margin: float, sms_cost: float) -> float:
    if depth <= 0:
        return 0.0
    u = uplift_by_depth(y, score, t, np.array([depth]))[0]
    return float(1000 * depth * (margin * u - sms_cost))


def sensitivity(curve: pd.DataFrame, margin: float, break_evens: list[float]) -> pd.DataFrame:
    """Optimal depth for other cost/margin ratios, reusing the measured uplift curve."""
    rows = []
    for be in break_evens:
        profit = 1000 * curve.depth * (margin * curve.uplift - be * margin)
        i = profit.idxmax()
        rows.append(
            {
                "break_even_uplift": be,
                "best_depth": curve.depth[i] if profit[i] > 0 else 0,
                "profit_per_1000": max(profit[i], 0.0),
            }
        )
    return pd.DataFrame(rows)
