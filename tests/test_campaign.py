import numpy as np

from uplift_engine.pipeline import assign_campaign, holdout_draw


def test_assignment_is_deterministic_and_order_independent():
    ids = [f"c{i}" for i in range(5000)]
    eligible = np.arange(5000) % 3 != 0
    a = assign_campaign(ids, eligible, "camp-1", 0.1, "v1").set_index("client_id")
    order = np.random.default_rng(0).permutation(5000)
    b = assign_campaign([ids[i] for i in order], eligible[order], "camp-1", 0.1, "v1")
    assert (b.set_index("client_id").action.loc[a.index] == a.action).all()


def test_adding_customers_does_not_change_existing_assignments():
    ids = [f"c{i}" for i in range(2000)]
    a = assign_campaign(ids, np.ones(2000, bool), "camp-1", 0.1, "v1").set_index("client_id")
    more = ids + [f"new{i}" for i in range(500)]
    b = assign_campaign(more, np.ones(2500, bool), "camp-1", 0.1, "v1").set_index("client_id")
    assert (b.loc[ids, "action"] == a.action).all()


def test_holdout_rate_and_campaign_independence():
    ids = [f"c{i}" for i in range(20000)]
    a = assign_campaign(ids, np.ones(20000, bool), "camp-1", 0.1, "v1")
    assert abs((a.action == "holdout").mean() - 0.1) < 0.01
    u1, u2 = holdout_draw("camp-1", ids), holdout_draw("camp-2", ids)
    assert abs(np.corrcoef(u1, u2)[0, 1]) < 0.05


def test_ineligible_never_sent_or_held_out():
    a = assign_campaign(["x", "y"], np.array([False, False]), "c", 0.5, "v")
    assert set(a.action) == {"ineligible"}
