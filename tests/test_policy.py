import pandas as pd
import pytest

from uplift_engine.config import Economics
from uplift_engine.policy import (
    best_depth,
    margin_per_purchase,
    profit_at,
    profit_curve,
    sensitivity,
)


def test_profit_curve_formula(experiment):
    y, t, s = experiment.target, experiment.treatment, experiment.true_uplift
    curve = profit_curve(y, s, t, margin=100, sms_cost=3, n_boot=20)
    row = curve.set_index("depth").loc[0.3]
    assert row.profit_per_1000 == pytest.approx(1000 * 0.3 * (100 * row.uplift - 3))
    assert profit_at(y, s, t, 0.3, 100, 3) == pytest.approx(row.profit_per_1000)
    assert (curve.profit_low <= curve.profit_high).all()


def test_nothing_profitable_means_depth_zero(experiment):
    curve = profit_curve(
        experiment.target,
        experiment.true_uplift,
        experiment.treatment,
        margin=1,
        sms_cost=100,
        n_boot=10,
    )
    assert best_depth(curve) == 0.0
    assert (
        profit_at(experiment.target, experiment.true_uplift, experiment.treatment, 0, 1, 100) == 0.0
    )


def test_cheaper_messages_mean_deeper_targeting():
    curve = pd.DataFrame({"depth": [0.1, 0.5, 1.0], "uplift": [0.10, 0.05, 0.02]})
    table = sensitivity(curve, margin=100, break_evens=[0.01, 0.04, 0.2])
    assert list(table.best_depth) == [0.5, 0.1, 0]


def test_margin_from_config_or_error():
    assert (
        margin_per_purchase(Economics(sms_cost=1, gross_margin=0.2, margin_per_purchase=50)) == 50
    )
    with pytest.raises(ValueError):
        margin_per_purchase(Economics(sms_cost=1, gross_margin=0.2))
