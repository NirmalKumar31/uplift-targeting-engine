# Methodology

## The question

A retailer can text a promotion to any of its 400k loyalty-card holders. Each SMS costs money,
and some clients would buy anyway. The question isn't *who will buy?*, it's **whose purchase
does the SMS cause?** That quantity is the individual treatment effect, or uplift:

```
tau(x) = P(buy | x, SMS) - P(buy | x, no SMS)
```

It is never observed for a single person (each client either got the SMS or didn't), but it can
be estimated because the data comes from a **randomised experiment**: X5 RetailHero texted a random
half of 200k clients. Measured covariates are balanced between arms (`ml.balance`: standardised mean
difference < 0.1); that is a diagnostic consistent with the documented random assignment, not proof
that randomisation worked.

## Data and features (dbt + DuckDB)

| Layer | Models | Notes |
|---|---|---|
| `raw` | 5 tables, 45.8M purchase lines | landed by `ute ingest`, SHA-256 verified, explicit column types |
| `staging` | `stg_clients`, `stg_transactions`, `stg_experiment` | ages outside 14-100 nulled; `first_redeem_date` values after the reference date dropped (they run to Nov 2019 and are partly caused by the SMS: post-treatment leakage, guarded by the `no_future_dates` test); line items collapsed to 8.0M transactions so basket sums aren't multiplied by basket size |
| `features` | 6 feature groups → `client_features` (47 columns) | all measured as of `reference_date` 2019-03-19, the day after the last purchase, so nothing after the SMS leaks in |
| `marts` | 16 BI tables | built on top of model outputs (`ml` schema) |

Feature groups: demographics and card tenure, RFM and basket shape, short-horizon activity and
momentum (7/14/30/60-day windows), **loyalty-points behaviour** (express points are
promotion-driven, so they are the closest proxy for past responsiveness to offers), shopping
habits (hour, weekend), and product mix (own brand, alcohol, department shares, category breadth).
dbt data tests guard keys, accepted values and ranges at every layer.

## Learners

All learners share one LightGBM configuration (`configs/pipeline.yaml`) and are implemented in
`src/uplift_engine/models/learners.py`:

| Learner | Idea |
|---|---|
| S-learner | one model with treatment as a feature; uplift = f(x,1) − f(x,0) |
| T-learner | one model per arm; uplift = μ₁(x) − μ₀(x) |
| X-learner | impute individual effects from the opposite arm's model, regress them, blend by propensity |
| Class transformation | z = 1 if (SMS ∧ bought) ∨ (no SMS ∧ didn't); uplift = 2P(z=1) − 1 |
| DR-learner | regress the doubly-robust pseudo-outcome, with 2-fold cross-fitted nuisance models |
| Ensemble | mean of the top-k learners' uplift estimates (same units, so averaging keeps scale) |
| *response* (baseline) | P(buy \| SMS): the traditional "target likely buyers" approach |
| *random* (baseline) | sanity floor |

## Evaluation protocol

1. **Locked test split.** 20% of the experiment (40,008 clients), stratified on treatment ×
   outcome, is set aside before anything is fitted.
2. **Cross-fitting on development.** Every learner is fitted 5 times on the remaining 160k
   clients; each client is scored by the fold model that never saw them. These out-of-fold scores
   drive every development decision: champion choice, ensemble members, and targeting depth.
3. **Fixed selection rule.** The champion is the highest out-of-fold Qini among uplift learners
   (including the ensemble), decided before test is touched.
4. **Test access (historical).** `ute evaluate` fits the champion on development data and scores
   test, logging each call to `meta.test_access_log`. The split was scored twice (before and after
   the leakage fix) and reused by the 2026-10-25 audit, so it is a reused holdout. The log covers only
   `pipeline.evaluate()`; it is an audit aid, not a lock.
5. **Uncertainty (withdrawn).** The original bootstrap resampled within treatment × outcome cells,
   which froze the response counts and understated uncertainty (a zero-width blanket-profit
   interval). Those intervals are withdrawn pending the corrected arm-level bootstrap.

Metrics match scikit-uplift's definitions (cross-checked to 10 decimal places) but are
implemented directly and vectorised:

- **Qini AUC** (normalised): area between the model's Qini curve and the random line, divided by
  the same area for a perfect ranking. 0 = random, 1 = perfect.
- **Uplift@k**: treated minus control purchase rate among the top k% by score.

## From ranking to decision

**Calibration.** Out-of-fold scores are cut into 20 quantile bins; each bin's observed uplift is
regressed on its median score with size-weighted isotonic regression. That gives every client an
*expected uplift in probability points*, a number the economics can use.

**Policy.** With an SMS cost `c` and a margin `m` per extra purchase, texting a client pays when
`expected_uplift × m > c`, i.e. when expected uplift exceeds the break-even `c / m`. At the
portfolio level, texting the top share `k` earns `k × (m × uplift@k − c)` per client; the
profit curve and its bootstrap band are in `ml.profit_curves`, with a sensitivity table for other
cost/margin ratios.

**Control group.** 10% of the selected clients are randomly held back (`action = 'holdout'`) so
a live campaign can measure its realised lift against the prediction.

**Segments.** Persuadable (expected uplift ≥ break-even), Sure thing (low uplift, P(buy without
SMS) ≥ 0.7), Lost cause (low uplift, low base rate), Do not disturb (expected uplift ≤ 0).

## Monitoring and explainability

- **Drift.** PSI of each feature, experiment clients vs the 200k clients outside the experiment
  (the population the model will actually score). Warn at 0.10, fail at 0.25.
- **Drivers.** A LightGBM surrogate is fitted to the deployed scores (so it works for ensembles
  too), and exact TreeSHAP (`pred_contrib`) attributes each score to the features. The
  surrogate's R² is reported so you know how faithful the explanation is. SHAP describes the
  model, not causation.

## Assumptions and limits

- Economics are assumptions: SMS ₽3; margin = mean basket × X5's reported 24.6% 2019 gross
  margin. Change `configs/pipeline.yaml`, or use the dashboard's ROI simulator.
- One snapshot, one campaign. The model learns how *this* SMS worked in early 2019.
- The uplift signal is real but modest. Most of the value sits in the top deciles, which is
  exactly where a targeted campaign spends its budget.
