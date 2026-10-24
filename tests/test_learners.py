import numpy as np
import pytest

from uplift_engine.data import prepare_features
from uplift_engine.models.learners import LEARNERS, UpliftCalibrator, build_learner

PARAMS = {"n_estimators": 60, "learning_rate": 0.1, "min_child_samples": 30, "verbose": -1}


@pytest.fixture(scope="module")
def xy(experiment):
    X = prepare_features(experiment.drop(columns=["treatment", "target", "true_uplift"]))
    return X, experiment.treatment.to_numpy(), experiment.target.to_numpy(), experiment.true_uplift


@pytest.mark.parametrize(
    "name", ["s_learner", "t_learner", "x_learner", "class_transformation", "dr_learner"]
)
def test_uplift_learners_recover_the_effect(xy, name):
    X, t, y, tau = xy
    pred = build_learner(name, PARAMS).fit(X, t, y).predict(X)
    assert pred.shape == (len(X),)
    assert np.corrcoef(pred, tau)[0, 1] > 0.3


def test_ensemble_averages_members(xy):
    X, t, y, _ = xy
    ens = build_learner("ensemble:s_learner+t_learner", PARAMS).fit(X, t, y)
    members = [mdl.predict(X) for mdl in ens.models_]
    np.testing.assert_allclose(ens.predict(X), np.mean(members, axis=0))


def test_baselines_flagged():
    assert LEARNERS["random"].is_baseline and LEARNERS["response"].is_baseline
    assert not LEARNERS["dr_learner"].is_baseline


def test_unknown_learner():
    with pytest.raises(KeyError):
        build_learner("nope")


def test_random_is_deterministic(xy):
    X, t, y, _ = xy
    a = build_learner("random", seed=3).fit(X, t, y).predict(X)
    b = build_learner("random", seed=3).fit(X, t, y).predict(X)
    np.testing.assert_array_equal(a, b)


def test_calibrator_is_monotone_and_on_uplift_scale(xy):
    _, t, y, tau = xy
    score = tau + np.random.default_rng(0).normal(0, 0.05, len(tau))
    cal = UpliftCalibrator(n_bins=10).fit(score, t, y)
    out = cal.transform(np.sort(score))
    assert np.all(np.diff(out) >= -1e-12)
    ate = y[t == 1].mean() - y[t == 0].mean()
    assert cal.transform(score).mean() == pytest.approx(ate, abs=0.03)
