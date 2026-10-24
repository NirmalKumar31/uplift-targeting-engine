"""Modeling frames from the feature store, and the locked development/test split."""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedKFold, train_test_split

CATEGORICAL = ["gender"]


def prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    """Model-ready matrix: client_id as index, booleans as 0/1, categoricals as pandas category."""
    X = df.set_index("client_id") if "client_id" in df.columns else df.copy()
    for col in X.columns:
        if col in CATEGORICAL:
            X[col] = pd.Categorical(X[col], categories=["F", "M", "U"])
        elif X[col].dtype == bool:
            X[col] = X[col].astype(int)
        else:
            X[col] = X[col].astype(float)
    return X


def load_features(con: duckdb.DuckDBPyConnection, client_ids: pd.Index | None = None):
    df = con.execute("SELECT * FROM features.client_features ORDER BY client_id").df()
    X = prepare_features(df)
    return X if client_ids is None else X.loc[client_ids]


def load_experiment(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return (
        con.execute(
            "SELECT client_id, treatment, target FROM staging.stg_experiment ORDER BY client_id"
        )
        .df()
        .set_index("client_id")
    )


def make_split(experiment: pd.DataFrame, test_share: float, seed: int) -> pd.Series:
    """Stratified on treatment x target so both partitions keep the experiment's balance."""
    strata = experiment.treatment * 2 + experiment.target
    _, test = train_test_split(
        np.arange(len(experiment)), test_size=test_share, stratify=strata, random_state=seed
    )
    split = np.full(len(experiment), "dev", dtype=object)
    split[test] = "test"
    return pd.Series(split, index=experiment.index, name="split")


def cv_folds(t: np.ndarray, y: np.ndarray, n_folds: int, seed: int):
    return list(StratifiedKFold(n_folds, shuffle=True, random_state=seed).split(t, t * 2 + y))
