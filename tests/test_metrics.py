import json
from pathlib import Path

import numpy as np
import pytest

from uplift_engine.evaluation import metrics as m

REF = json.loads((Path(__file__).parent / "fixtures" / "qini_reference.json").read_text())


@pytest.mark.parametrize("case", REF["cases"], ids=lambda c: c["name"])
def test_matches_scikit_uplift_reference(case):
    y, s, t = (np.asarray(case[k]) for k in ("y", "score", "t"))
    assert m.qini_auc(y, s, t) == pytest.approx(case["qini_auc"], abs=1e-12)
    for k, expected in case["uplift_at_k"].items():
        assert m.uplift_at_k(y, s, t, float(k)) == pytest.approx(expected, abs=1e-12)


def test_reference_covers_required_cases():
    names = {c["name"] for c in REF["cases"]}
    assert {"heavily_tied_scores", "negative_effect", "unequal_arms_20pct_treated"} <= names
    assert REF["provenance"]["scikit_uplift"] == "0.5.1"


def test_perfect_ranking_scores_one(experiment):
    y, t = experiment.target.to_numpy(), experiment.treatment.to_numpy()
    assert m.qini_auc(y, y * t - y * (1 - t), t) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("y", "s", "t", "msg"),
    [
        ([1, 0], [0.1], [1, 0], "same length"),
        ([], [], [], "empty"),
        ([1, 2], [0.1, 0.2], [1, 0], "binary"),
        ([1, 0], [0.1, 0.2], [1, 3], "binary"),
        ([1, 0], [np.nan, 0.2], [1, 0], "NaN or infinite"),
        ([1, 0], [np.inf, 0.2], [1, 0], "NaN or infinite"),
        ([1, 0], [0.1, 0.2], [1, 1], "both treatment arms"),
    ],
)
def test_input_contract(y, s, t, msg):
    with pytest.raises(m.MetricInputError, match=msg):
        m.qini_auc(y, s, t)


def test_invalid_depths_and_undefined_results():
    y, s, t = np.array([1, 0, 1, 0]), np.array([4.0, 3, 2, 1]), np.array([1, 1, 0, 0])
    for bad in (0, -0.1, 1.5):
        with pytest.raises(m.MetricInputError):
            m.uplift_at_k(y, s, t, bad)
    with pytest.raises(m.UndefinedMetricError):  # top half contains no control customer
        m.uplift_at_k(y, s, t, 0.5)
    assert np.isnan(m.uplift_by_depth(y, s, t, [0.5])[0])
    with pytest.raises(m.UndefinedMetricError):  # no buyers: perfect area == random area
        m.qini_auc(np.zeros(4), s, t)


def test_arm_bootstrap_fixes_arm_sizes_and_lets_responses_vary(experiment):
    t, y = experiment.treatment.to_numpy(), experiment.target.to_numpy()
    draws = m.arm_bootstrap(t, n_boot=50, seed=1)
    treated = [t[i].sum() for i in draws.indices]
    responders = [y[i][t[i] == 1].sum() for i in draws.indices]
    assert set(treated) == {t.sum()}
    assert len(set(responders)) > 10
    assert draws.spec["resampling_unit"] == "customer"


def test_blanket_effect_interval_has_nonzero_width():
    rng = np.random.default_rng(0)
    t = rng.integers(0, 2, 4000)
    y = (rng.random(4000) < 0.5 + 0.05 * t).astype(int)
    ci = m.bootstrap(
        lambda a, s, b: m.uplift_at_k(a, s, b, 1.0), y, np.zeros(4000), t, n_boot=200, seed=0
    )
    assert ci.high - ci.low > 0.01


def test_paired_identical_scores_give_exact_zero(experiment):
    y, t, s = experiment.target, experiment.treatment, experiment.true_uplift
    res = m.paired_bootstrap(m.qini_auc, y, s, s, t, n_boot=50)
    assert res.difference.estimate == 0 and res.difference.low == 0 and res.difference.high == 0
    assert res.frac_positive == 0 and res.frac_zero == 1


def test_known_effect_detected_and_null_not(experiment):
    y, t = experiment.target.to_numpy(), experiment.treatment.to_numpy()
    good = m.bootstrap(m.qini_auc, y, experiment.true_uplift, t, n_boot=100, seed=2)
    assert good.low > 0
    noise = np.random.default_rng(3).random(len(y))
    null = m.bootstrap(m.qini_auc, y, noise, t, n_boot=100, seed=2)
    assert null.low < 0 < null.high


def test_undefined_replicates_are_counted_not_zeroed():
    # Point estimate is defined; some resamples have only one arm in the top 2.
    t = np.array([1, 0, 1, 0, 1, 0])
    y = np.array([1, 0, 1, 0, 1, 0])
    s = np.arange(6.0)
    ci = m.bootstrap(lambda a, b, c: m.uplift_at_k(a, b, c, 0.34), y, s, t, n_boot=60, seed=0)
    assert ci.n_undefined > 0
    assert ci.n_boot == 60


def test_noise_floor_brackets_zero(experiment):
    lo, hi = m.random_noise_floor(experiment.target, experiment.treatment, n_rankings=50)
    assert lo < 0 < hi


def test_decile_table(experiment):
    d = m.decile_table(experiment.target, experiment.true_uplift, experiment.treatment)
    assert list(d.decile) == list(range(1, 11)) and d.n.sum() == len(experiment)
