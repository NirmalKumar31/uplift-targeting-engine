from uplift_engine.evaluation.diagnostics import coverage


def test_coverage_diagnostic_runs_and_is_plausible():
    res = coverage(n_experiments=40, n=1500, n_boot=100, seed=5)
    assert 0.75 <= res["ate"]["coverage"] <= 1.0
    assert 0.75 <= res["policy_value"]["coverage"] <= 1.0
    assert res["mean_ate_interval_width"] > 0
    assert res["ate"]["monte_carlo_se"] > 0
