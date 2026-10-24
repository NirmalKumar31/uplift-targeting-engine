import pandas as pd

from uplift_engine.data import cv_folds, make_split, prepare_features


def test_split_is_stratified_and_reproducible(experiment):
    exp = experiment.set_index("client_id")[["treatment", "target"]]
    s1, s2 = make_split(exp, 0.2, 7), make_split(exp, 0.2, 7)
    pd.testing.assert_series_equal(s1, s2)
    assert abs((s1 == "test").mean() - 0.2) < 0.01
    by_cell = exp.groupby(exp.treatment * 2 + exp.target).apply(
        lambda g: (s1.loc[g.index] == "test").mean()
    )
    assert by_cell.between(0.19, 0.21).all()


def test_prepare_features_types(experiment):
    X = prepare_features(experiment[["client_id", "gender", "has_redeemed", "age"]])
    assert X.index.name == "client_id"
    assert str(X.gender.dtype) == "category"
    assert X.has_redeemed.dtype == int
    assert X.age.dtype == float


def test_folds_partition(experiment):
    folds = cv_folds(experiment.treatment.to_numpy(), experiment.target.to_numpy(), 3, 0)
    held_out = sorted(i for _, v in folds for i in v)
    assert held_out == list(range(len(experiment)))
