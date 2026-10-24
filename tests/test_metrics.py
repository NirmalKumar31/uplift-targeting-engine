import numpy as np
import pytest

from uplift_engine.evaluation import metrics as m


def test_perfect_ranking_scores_one_and_random_near_zero(experiment):
    y, t = experiment.target.to_numpy(), experiment.treatment.to_numpy()
    perfect = y * t - y * (1 - t)
    assert m.qini_auc(y, perfect, t) == pytest.approx(1.0)
    rand = np.random.default_rng(0).random(len(y))
    assert abs(m.qini_auc(y, rand, t)) < 0.1


def test_true_uplift_beats_random(experiment):
    y, t = experiment.target.to_numpy(), experiment.treatment.to_numpy()
    assert m.qini_auc(y, experiment.true_uplift, t) > 0.1


def test_uplift_by_depth_matches_uplift_at_k(experiment):
    y, t = experiment.target.to_numpy(), experiment.treatment.to_numpy()
    s = experiment.true_uplift.to_numpy() + np.random.default_rng(1).normal(0, 0.05, len(y))
    depths = np.array([0.1, 0.3, 0.5, 0.9])
    expected = [m.uplift_at_k(y, s, t, k) for k in depths]
    np.testing.assert_allclose(m.uplift_by_depth(y, s, t, depths), expected)


def test_qini_curve_starts_at_origin_and_handles_ties():
    y = np.array([1, 0, 1, 0, 1, 1])
    t = np.array([1, 0, 1, 1, 0, 0])
    x, q = m.qini_curve(y, np.ones(6), t)
    assert (x[0], q[0]) == (0, 0)
    assert len(x) == 2  # one distinct score -> one threshold


def test_length_mismatch_raises():
    with pytest.raises(ValueError):
        m.qini_auc([1, 0], [0.1], [1, 0])


def test_bootstrap_interval_brackets_estimate(experiment):
    y, t = experiment.target.to_numpy(), experiment.treatment.to_numpy()
    ci = m.bootstrap(m.qini_auc, y, experiment.true_uplift, t, n_boot=50)
    assert ci.low <= ci.estimate <= ci.high
    assert set(ci.as_dict("q")) == {"q", "q_low", "q_high"}


def test_paired_bootstrap_prefers_true_uplift(experiment):
    y, t = experiment.target.to_numpy(), experiment.treatment.to_numpy()
    rand = np.random.default_rng(0).random(len(y))
    diff, p = m.paired_bootstrap(m.qini_auc, y, experiment.true_uplift, rand, t, n_boot=50)
    assert diff.estimate > 0 and p > 0.9


def test_noise_floor_is_centred_on_zero(experiment):
    lo, hi = m.random_noise_floor(experiment.target, experiment.treatment, n_rankings=50)
    assert lo < 0 < hi


def test_decile_table_shape(experiment):
    d = m.decile_table(experiment.target, experiment.true_uplift, experiment.treatment)
    assert list(d.decile) == list(range(1, 11))
    assert d.n.sum() == len(experiment)
    assert d.observed_uplift.iloc[0] > d.observed_uplift.iloc[-1]
