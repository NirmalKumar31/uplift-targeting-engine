import numpy as np
import pandas as pd

from uplift_engine.config import Monitoring
from uplift_engine.data import prepare_features
from uplift_engine.monitoring import balance_table, psi, psi_table


def test_psi_zero_for_same_distribution_and_large_for_shift():
    rng = np.random.default_rng(0)
    a = pd.Series(rng.normal(size=5000))
    assert psi(a, pd.Series(rng.normal(size=5000))) < 0.02
    assert psi(a, pd.Series(rng.normal(1.5, 1, size=5000))) > 0.25


def test_psi_table_is_labelled_a_diagnostic():
    rng = np.random.default_rng(1)
    ref = pd.DataFrame({"x": rng.normal(size=3000)})
    out = psi_table(ref, pd.DataFrame({"x": rng.normal(2, 1, 3000)}), Monitoring())
    assert out.status.iloc[0] == "high" and "not a deployment gate" in out.role.iloc[0]


def test_balance_reports_categories_and_missingness():
    rng = np.random.default_rng(2)
    n = 4000
    t = rng.integers(0, 2, n)
    df = pd.DataFrame(
        {
            "client_id": [f"c{i}" for i in range(n)],
            "gender": np.where(t == 1, rng.choice(["F", "M"], n), "U"),
            "age": np.where((t == 1) & (rng.random(n) < 0.5), np.nan, 40.0),
        }
    )
    X = prepare_features(df)
    table = balance_table(X, pd.Series(t, index=X.index))
    checks = set(zip(table.feature, table.check, strict=True))
    assert ("gender", "category share") in checks and ("age", "missingness") in checks
    assert not table.query("feature == 'gender'").balanced.all()  # imbalance a mean would hide
    assert not table.query("check == 'missingness'").balanced.all()
