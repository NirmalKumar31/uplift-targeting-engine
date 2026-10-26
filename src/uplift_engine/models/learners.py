"""Uplift meta-learners on a shared LightGBM base."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import StratifiedKFold


def _clf(params: dict[str, Any], seed: int) -> lgb.LGBMClassifier:
    return lgb.LGBMClassifier(**params, random_state=seed)


def _reg(params: dict[str, Any], seed: int) -> lgb.LGBMRegressor:
    return lgb.LGBMRegressor(**params, random_state=seed)


def _proba(model: lgb.LGBMClassifier, X: pd.DataFrame) -> np.ndarray:
    return model.predict_proba(X)[:, 1]


class UpliftLearner(ABC):
    name: ClassVar[str]
    is_baseline: ClassVar[bool] = False

    def __init__(self, params: dict[str, Any] | None = None, seed: int = 42):
        self.params = dict(params or {})
        self.seed = seed

    @abstractmethod
    def fit(self, X: pd.DataFrame, t: np.ndarray, y: np.ndarray) -> UpliftLearner: ...

    @abstractmethod
    def predict(self, X: pd.DataFrame) -> np.ndarray: ...


class RandomLearner(UpliftLearner):
    name = "random"
    is_baseline = True

    def fit(self, X, t, y):
        return self

    def predict(self, X):
        return np.random.default_rng(self.seed).random(len(X))


class ResponseLearner(UpliftLearner):
    """Ranks by P(buy | SMS): the traditional likely-buyer approach."""

    name = "response"
    is_baseline = True

    def fit(self, X, t, y):
        t = np.asarray(t)
        self.model_ = _clf(self.params, self.seed).fit(X[t == 1], np.asarray(y)[t == 1])
        return self

    def predict(self, X):
        return _proba(self.model_, X)


class SLearner(UpliftLearner):
    """One model with treatment as a feature."""

    name = "s_learner"

    def fit(self, X, t, y):
        self.model_ = _clf(self.params, self.seed).fit(X.assign(_t=np.asarray(t)), y)
        return self

    def predict(self, X):
        return _proba(self.model_, X.assign(_t=1)) - _proba(self.model_, X.assign(_t=0))


class TLearner(UpliftLearner):
    """One model per arm."""

    name = "t_learner"

    def fit(self, X, t, y):
        t, y = np.asarray(t), np.asarray(y)
        self.mu1_ = _clf(self.params, self.seed).fit(X[t == 1], y[t == 1])
        self.mu0_ = _clf(self.params, self.seed).fit(X[t == 0], y[t == 0])
        return self

    def predict(self, X):
        return _proba(self.mu1_, X) - _proba(self.mu0_, X)

    def predict_arms(self, X) -> tuple[np.ndarray, np.ndarray]:
        return _proba(self.mu0_, X), _proba(self.mu1_, X)


class XLearner(UpliftLearner):
    """Kunzel et al. (2019)."""

    name = "x_learner"

    def fit(self, X, t, y):
        t, y = np.asarray(t), np.asarray(y, dtype=float)
        base = TLearner(self.params, self.seed).fit(X, t, y)
        d1 = y[t == 1] - _proba(base.mu0_, X[t == 1])
        d0 = _proba(base.mu1_, X[t == 0]) - y[t == 0]
        self.tau1_ = _reg(self.params, self.seed).fit(X[t == 1], d1)
        self.tau0_ = _reg(self.params, self.seed).fit(X[t == 0], d0)
        self.e_ = t.mean()
        return self

    def predict(self, X):
        return self.e_ * self.tau0_.predict(X) + (1 - self.e_) * self.tau1_.predict(X)


class ClassTransformation(UpliftLearner):
    """Jaskowski & Jaroszewicz (2012). Arms are reweighted to 50/50 so 2P(z) - 1 holds."""

    name = "class_transformation"

    def fit(self, X, t, y):
        t, y = np.asarray(t), np.asarray(y)
        e = t.mean()
        if not 0 < e < 1:
            raise ValueError("class transformation needs both treatment arms")
        z = (y * t + (1 - y) * (1 - t)).astype(int)
        w = np.where(t == 1, 0.5 / e, 0.5 / (1 - e))
        self.model_ = _clf(self.params, self.seed).fit(X, z, sample_weight=w)
        return self

    def predict(self, X):
        return 2 * _proba(self.model_, X) - 1


class DRLearner(UpliftLearner):
    """Doubly robust learner with 2-fold cross-fitted outcome models."""

    name = "dr_learner"

    def fit(self, X, t, y):
        t, y = np.asarray(t), np.asarray(y, dtype=float)
        e = t.mean()
        mu0, mu1 = np.zeros(len(y)), np.zeros(len(y))
        folds = StratifiedKFold(2, shuffle=True, random_state=self.seed)
        for fit_idx, pred_idx in folds.split(X, t * 2 + y):
            arms = TLearner(self.params, self.seed).fit(X.iloc[fit_idx], t[fit_idx], y[fit_idx])
            mu0[pred_idx], mu1[pred_idx] = arms.predict_arms(X.iloc[pred_idx])
        pseudo = mu1 - mu0 + t * (y - mu1) / e - (1 - t) * (y - mu0) / (1 - e)
        self.model_ = _reg(self.params, self.seed).fit(X, pseudo)
        return self

    def predict(self, X):
        return self.model_.predict(X)


class EnsembleLearner(UpliftLearner):
    """Mean of member uplift estimates."""

    name = "ensemble"

    def __init__(self, members: list[str], params=None, seed: int = 42):
        super().__init__(params, seed)
        self.members = members

    def fit(self, X, t, y):
        self.models_ = [build_learner(m, self.params, self.seed).fit(X, t, y) for m in self.members]
        return self

    def predict(self, X):
        return np.mean([m.predict(X) for m in self.models_], axis=0)


LEARNERS: dict[str, type[UpliftLearner]] = {
    cls.name: cls
    for cls in (
        RandomLearner,
        ResponseLearner,
        SLearner,
        TLearner,
        XLearner,
        ClassTransformation,
        DRLearner,
    )
}


def build_learner(name: str, params: dict[str, Any] | None = None, seed: int = 42) -> UpliftLearner:
    if name.startswith("ensemble:"):
        return EnsembleLearner(name.split(":", 1)[1].split("+"), params, seed)
    if name not in LEARNERS:
        raise KeyError(f"unknown learner {name!r}; choose from {sorted(LEARNERS)}")
    return LEARNERS[name](params, seed)


class UpliftCalibrator:
    """Isotonic map from score to uplift, fitted on observed uplift in score quantile bins."""

    def __init__(self, n_bins: int = 20):
        if n_bins < 2:
            raise ValueError("n_bins must be >= 2")
        self.n_bins = n_bins

    def fit(self, score: np.ndarray, t: np.ndarray, y: np.ndarray) -> UpliftCalibrator:
        df = pd.DataFrame({"s": np.asarray(score, float), "t": np.asarray(t), "y": np.asarray(y)})
        df["bin"] = pd.qcut(df.s.rank(method="first"), self.n_bins, labels=False)
        g = df.groupby("bin")
        rates = df.pivot_table(index="bin", columns="t", values="y", aggfunc="mean")
        self.bins_ = pd.DataFrame(
            {"score": g.s.median(), "n": g.size(), "uplift": rates[1] - rates[0]}
        )
        self.iso_ = IsotonicRegression(increasing=True, out_of_bounds="clip")
        self.iso_.fit(self.bins_.score, self.bins_.uplift, sample_weight=self.bins_.n)
        return self

    def transform(self, score: np.ndarray) -> np.ndarray:
        return self.iso_.predict(np.asarray(score, dtype=float))
