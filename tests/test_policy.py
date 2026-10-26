import numpy as np
import pandas as pd
import pytest

from uplift_engine.config import Economics
from uplift_engine.evaluation import metrics as m
from uplift_engine.policy import (
    best_depth,
    evaluate_policies,
    expected_random_value,
    margin_per_purchase,
    policy_value,
    profit_curve,
    select_by_effect,
    select_top,
    sensitivity,
)


def test_policy_value_formula_and_edge_cases():
    y = np.array([1, 0, 1, 1, 0, 0, 1, 0])
    t = np.array([1, 1, 0, 0, 1, 0, 1, 0])
    sel = np.array([1, 1, 1, 1, 0, 0, 0, 0], bool)
    lift = 0.5 - 1.0
    assert policy_value(y, t, sel, 100, 3) == pytest.approx(1000 * 0.5 * (100 * lift - 3))
    assert policy_value(y, t, np.zeros(8, bool), 100, 3) == 0.0
    with pytest.raises(m.UndefinedMetricError):
        policy_value(y, t, np.array([1, 1, 0, 0, 0, 0, 0, 0], bool), 100, 3)


def test_strict_threshold_semantics():
    sel = select_by_effect(np.array([0.02, 0.03, 0.04]), margin=100, sms_cost=3)
    assert sel.tolist() == [False, False, True]  # 0.03 * 100 == 3 is NOT selected


def test_select_top():
    assert select_top(np.array([0.1, 0.9, 0.5, 0.7]), 0.5).tolist() == [False, True, False, True]
    with pytest.raises(m.MetricInputError):
        select_top(np.zeros(3), 1.5)


def test_evaluate_policies_paired_and_baselines(experiment):
    y, t = experiment.target.to_numpy(), experiment.treatment.to_numpy()
    draws = m.arm_bootstrap(t, 100, 0)
    good = select_top(experiment.true_uplift.to_numpy(), 0.3)
    vals, diffs = evaluate_policies(
        y,
        t,
        {"good": good, "good_copy": good.copy()},
        100,
        3,
        draws,
        references=("blanket", "no_send", "good"),
        random_shares={"random_30": 0.3},
    )
    v = vals.set_index("policy")
    assert v.loc["no_send", "value_per_1000"] == 0
    assert v.loc["blanket", "value_high"] > v.loc["blanket", "value_low"]
    assert v.loc["random_30", "value_per_1000"] == pytest.approx(
        expected_random_value(y, t, 0.3, 100, 3)
    )
    same = diffs.query("policy == 'good_copy' and reference == 'good'").iloc[0]
    assert same.difference_per_1000 == 0 and same.difference_low == 0 == same.difference_high
    vs_blanket = diffs.query("policy == 'good' and reference == 'blanket'").iloc[0]
    assert vs_blanket.difference_low > 0
    assert {"frac_positive", "n_undefined"} <= set(diffs.columns)
    assert v.loc["good", "n_selected_treated"] + v.loc["good", "n_selected_control"] == good.sum()


def test_profit_curve_uses_given_draws(experiment):
    y, t, s = experiment.target, experiment.treatment, experiment.true_uplift
    draws = m.arm_bootstrap(t, 37, 0)
    curve = profit_curve(y, s, t, 100, 3, draws)
    last = curve.iloc[-1]
    assert last.depth == 1.0 and last.value_high > last.value_low
    assert (curve.value_low <= curve.value_high + 1e-9).all()


def test_best_depth_is_no_send_when_nothing_pays():
    curve = pd.DataFrame({"depth": [0.5, 1.0], "value_per_1000": [-1.0, -2.0]})
    assert best_depth(curve) == 0.0


def test_sensitivity():
    curve = pd.DataFrame({"depth": [0.1, 0.5, 1.0], "uplift": [0.10, 0.05, 0.02]})
    table = sensitivity(curve, margin=100, break_evens=[0.01, 0.04, 0.2])
    assert list(table.best_depth) == [0.5, 0.1, 0]


def test_margin_must_be_positive():
    assert (
        margin_per_purchase(Economics(sms_cost=1, gross_margin=0.2, margin_per_purchase=50)) == 50
    )
    with pytest.raises(ValueError):
        margin_per_purchase(Economics(sms_cost=1, gross_margin=0.2))
