"""Coverage of bootstrap intervals over simulated experiments with a known effect."""

from __future__ import annotations

import numpy as np

from uplift_engine.evaluation import metrics as m
from uplift_engine.policy import evaluate_policies


def coverage(
    n_experiments: int = 200,
    n: int = 4000,
    n_boot: int = 300,
    effect: float = 0.03,
    heterogeneity: float = 0.06,
    margin: float = 100.0,
    sms_cost: float = 3.0,
    seed: int = 0,
) -> dict:
    rng = np.random.default_rng(seed)
    x_pop = rng.random(200_000)
    tau_pop = effect + heterogeneity * (x_pop - 0.5)
    true_ate = float(tau_pop.mean())
    top = x_pop > 0.7
    true_value = float(1000 * top.mean() * (margin * tau_pop[top].mean() - sms_cost))

    hits_ate, hits_val, widths = [], [], []
    for _ in range(n_experiments):
        x = rng.random(n)
        t = rng.integers(0, 2, n)
        p = np.clip(0.4 + t * (effect + heterogeneity * (x - 0.5)), 0, 1)
        y = (rng.random(n) < p).astype(int)
        draws = m.arm_bootstrap(t, n_boot, int(rng.integers(1 << 31)))
        reps = draws.replicates(lambda i, y=y, t=t: m.ate(y[i], t[i]))
        ci = draws.interval(m.ate(y, t), reps)
        hits_ate.append(ci.low <= true_ate <= ci.high)
        widths.append(ci.high - ci.low)
        vals, _ = evaluate_policies(y, t, {"top": x > 0.7}, margin, sms_cost, draws, references=())
        row = vals.set_index("policy").loc["top"]
        hits_val.append(row.value_low <= true_value <= row.value_high)

    def summary(h):
        p = float(np.mean(h))
        return {"coverage": p, "monte_carlo_se": float(np.sqrt(p * (1 - p) / len(h)))}

    return {
        "n_experiments": n_experiments,
        "n_per_experiment": n,
        "n_boot": n_boot,
        "nominal": 0.95,
        "true_ate": true_ate,
        "true_policy_value_per_1000": true_value,
        "ate": summary(hits_ate),
        "policy_value": summary(hits_val),
        "mean_ate_interval_width": float(np.mean(widths)),
        "seed": seed,
    }
