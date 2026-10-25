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
    assert np.corrcoef(pred, tau)[0, 1] > 0.3


def test_class_transformation_reweights_unequal_arms(xy):
    X, _, _, tau = xy
    rng = np.random.default_rng(4)
    t = (rng.random(len(X)) < 0.2).astype(int)
    y = (rng.random(len(X)) < np.clip(0.4 + t * tau, 0, 1)).astype(int)
    pred = build_learner("class_transformation", PARAMS).fit(X, t, y).predict(X)
    true_mean = float(np.mean(tau))
    assert abs(pred.mean() - true_mean) < 0.08  # unweighted CT would be biased by ~0.6 here
    assert np.corrcoef(pred, tau)[0, 1] > 0.3


def test_ensemble_averages_members(xy):
    X, t, y, _ = xy
    ens = build_learner("ensemble:s_learner+t_learner", PARAMS).fit(X, t, y)
    np.testing.assert_allclose(ens.predict(X), np.mean([e.predict(X) for e in ens.models_], 0))


def test_baselines_flagged_and_unknown_rejected():
    assert LEARNERS["random"].is_baseline and not LEARNERS["dr_learner"].is_baseline
    with pytest.raises(KeyError):
        build_learner("nope")


def test_calibrator_is_monotone_and_on_uplift_scale(xy):
    _, t, y, tau = xy
    score = tau + np.random.default_rng(0).normal(0, 0.05, len(tau))
    cal = UpliftCalibrator(n_bins=10).fit(score, t, y)
    assert np.all(np.diff(cal.transform(np.sort(score))) >= -1e-12)
    assert cal.transform(score).mean() == pytest.approx(
        y[t == 1].mean() - y[t == 0].mean(), abs=0.03
    )
    with pytest.raises(ValueError):
        UpliftCalibrator(n_bins=1)
