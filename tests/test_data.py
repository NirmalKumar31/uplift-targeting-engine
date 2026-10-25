from uplift_engine.data import cv_folds, prepare_features


def test_prepare_features_types(experiment):
    X = prepare_features(experiment[["client_id", "gender", "has_redeemed", "age"]])
    assert X.index.name == "client_id"
    assert str(X.gender.dtype) == "category"
    assert X.has_redeemed.dtype == float and X.age.dtype == float


def test_folds_partition(experiment):
    folds = cv_folds(experiment.treatment.to_numpy(), experiment.target.to_numpy(), 3, 0)
    assert sorted(i for _, v in folds for i in v) == list(range(len(experiment)))
