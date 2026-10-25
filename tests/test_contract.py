import math

import numpy as np
import pandas as pd
import pytest

from uplift_engine.contract import ContractError, FeatureContract, to_model_frame
from uplift_engine.data import prepare_features


@pytest.fixture(scope="module")
def contract():
    rng = np.random.default_rng(0)
    n = 200
    X = prepare_features(
        pd.DataFrame(
            {
                "client_id": [f"c{i}" for i in range(n)],
                "age": rng.integers(14, 90, n),
                "gender": rng.choice(["F", "M", "U"], n),
                "has_redeemed": rng.random(n) < 0.5,
                "n_txn": rng.integers(1, 50, n),
                "recency_days": rng.random(n) * 20,
                "spend_total": rng.random(n) * 1e4,
                "card_tenure_days": rng.random(n) * 700,
                "share_own_brand": rng.random(n),
            }
        )
    )
    return FeatureContract.from_training(X)


GOOD = {
    "client_id": "a",
    "age": 40,
    "gender": "F",
    "has_redeemed": True,
    "n_txn": 5,
    "recency_days": 3.5,
    "spend_total": 1200.0,
    "card_tenure_days": 300.0,
    "share_own_brand": 0.2,
}


def test_valid_record_matches_training_dtypes(contract):
    X, comp = contract.validate_records([GOOD])
    assert list(X.columns) == contract.columns and comp == [1.0]
    assert str(X.gender.dtype) == "category" and X.has_redeemed.iloc[0] == 1.0


def test_optional_feature_may_be_omitted_or_null(contract):
    rec = {k: v for k, v in GOOD.items() if k != "share_own_brand"} | {"age": None}
    X, comp = contract.validate_records([rec])
    assert math.isnan(X.age.iloc[0]) and comp[0] < 1


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"client_id": "x"},
        GOOD | {"gender": "X"},
        GOOD | {"age": -999},
        GOOD | {"age": "40"},
        GOOD | {"spend_total": float("inf")},
        GOOD | {"spend_total": "Infinity"},
        GOOD | {"share_own_brand": 1.5},
        GOOD | {"has_redeemed": "yes"},
        GOOD | {"bogus": 1},
        GOOD | {"n_txn": None},
    ],
)
def test_unusable_or_invalid_records_rejected(contract, bad):
    with pytest.raises(ContractError):
        contract.validate_records([bad])


def test_errors_identify_record_and_field(contract):
    with pytest.raises(ContractError) as exc:
        contract.validate_records([GOOD, GOOD | {"age": 500}])
    assert exc.value.errors[0]["index"] == 1 and "age" in exc.value.errors[0]["errors"][0]


def test_training_frame_outside_domain_is_rejected():
    X = prepare_features(
        pd.DataFrame(
            {
                "client_id": ["a"],
                "gender": ["F"],
                "n_txn": [-1.0],
                "recency_days": [1.0],
                "spend_total": [1.0],
                "card_tenure_days": [1.0],
            }
        )
    )
    with pytest.raises(ValueError, match="n_txn"):
        FeatureContract.from_training(X)


def test_model_frame_parity_with_training_prep(contract):
    X_api, _ = contract.validate_records([GOOD])
    X_train = prepare_features(pd.DataFrame([GOOD]))
    pd.testing.assert_frame_equal(
        X_api.reset_index(drop=True), X_train[contract.columns].reset_index(drop=True)
    )
    assert to_model_frame(pd.DataFrame([GOOD]), contract).shape[1] == len(contract.columns)
