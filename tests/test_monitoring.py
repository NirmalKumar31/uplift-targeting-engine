import numpy as np
import pandas as pd

from uplift_engine.config import Monitoring
from uplift_engine.monitoring import balance_table, psi, psi_table


def test_psi_zero_for_same_distribution_and_large_for_shift():
    rng = np.random.default_rng(0)
    a = pd.Series(rng.normal(size=5000))
    assert psi(a, pd.Series(rng.normal(size=5000))) < 0.02
    assert psi(a, pd.Series(rng.normal(1.5, 1, size=5000))) > 0.25


def test_psi_counts_missing_and_low_cardinality():
    a = pd.Series([0, 1, 1, 2, None] * 200, dtype=float)
    b = pd.Series([0, 1, 1, 2, 2] * 200, dtype=float)
    assert psi(a, a) == 0
    assert psi(a, b) > 0


def test_psi_table_status():
    rng = np.random.default_rng(1)
    ref = pd.DataFrame({"x": rng.normal(size=3000), "z": rng.normal(size=3000)})
    cur = pd.DataFrame({"x": rng.normal(2, 1, 3000), "z": rng.normal(size=3000)})
    out = psi_table(ref, cur, Monitoring()).set_index("feature")
    assert out.loc["x", "status"] == "fail" and out.loc["z", "status"] == "ok"


def test_balance_on_randomised_data(experiment):
    X = experiment[["age", "n_txn", "spend_total"]].set_index(experiment.client_id)
    t = experiment.set_index("client_id").treatment
    table = balance_table(X, t)
    assert table.balanced.all()
