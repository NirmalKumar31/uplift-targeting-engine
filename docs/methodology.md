# Methodology

This document describes what the pipeline estimates, how, and what each number does and does not
show. Numbers themselves live in the active release's `results.json`; the README's results block
is generated from it (`scripts/render_readme_results.py`).

## 1. Decision problem

| | |
|---|---|
| Population | X5 loyalty-card holders in the 2019 snapshot (400,162). Labelled: the 200,039 customers of the randomised SMS experiment. |
| Treatment | Receiving the campaign SMS (`treatment_flg`). Assignment was random according to the dataset documentation; we treat that as a design assumption. |
| Outcome | `target`: the customer purchased in the campaign's outcome window (binary; the horizon is defined by the competition, not by this project). |
| Effect | uplift τ(x) = P(buy \| x, SMS) − P(buy \| x, no SMS). |
| Assumed utility | value = margin × additional buyers − SMS cost, with margin = mean historical basket × X5's reported 24.6% gross margin (≈ ₽105) and SMS cost ₽3. **An assumption**: a constant basket margin does not identify causal incremental revenue or the number of extra transactions. Break-even uplift = cost / margin ≈ 2.85%. |
| Deployed action | Text a customer iff calibrated effect × margin **>** SMS cost (strict). In the frozen campaign, an eligible customer is held out iff sha256(campaign_id:client_id)/2⁶⁴ < 0.10. |

## 2. Point-in-time features (dbt)

`dataset.as_of` (2019-03-19 00:00, configured once) is a **feature cutoff**: every input must be
strictly earlier. The competition documentation says purchase history precedes the SMS; the exact
SMS timestamp is not published, so the cutoff is the day after the last purchase rather than a
claimed send time.

- All purchase features, including product mix, read `stg_purchase_lines` (`< as_of`). Source
  tests fail the build on purchase lines at or after the cutoff, null timestamps, contradictory
  transaction-level fields within (client, transaction), orphan client/product keys, train/test
  overlap and non-binary treatment/outcome.
- Windows are half-open elapsed-time intervals `[as_of − N days, as_of)`. Timestamps are store
  local time without a timezone.
- Cleaning rules are counted in `stg_data_quality`: redemptions after the cutoff (post-treatment,
  44,953) and before card issue (536) become unknown and are flagged; ages outside 14–100 (1,779)
  become unknown. Ages 14–17 are reported as their own band.

## 3. The policy procedure

Defined once (`pipeline.fit_procedure`) and used by both assessment and deployment:

1. 5-fold inner cross-fitting of every candidate (S, T, X, class-transformation with arm
   reweighting, DR; response and random baselines) on the training customers.
2. Champion = highest inner out-of-fold Qini among uplift learners and a top-3 mean ensemble.
3. Calibrator = size-weighted isotonic regression of observed uplift on the champion's inner OOF
   scores in 20 quantile bins.
4. Champion refitted on all training customers; calibrated effect → strict cost/margin rule.
5. Comparator: the response model, refitted, texting its top share chosen on its own inner OOF
   value curve, so it is optimised on training data too.

## 4. Evidence sources (not interchangeable)

**Outer-fold retrospective validation** (`ute assess`). 5 outer folds over all labelled customers.
The whole procedure (selection, calibration, threshold, comparator) is fitted on 4 folds and
applied to the 5th; validation outcomes never enter selection or calibration. Each customer is
validated once, and their selection flags are pooled. Policy value is estimated by the difference
in means within the selected group (unbiased under random assignment), scaled per 1,000 customers
of the evaluated population; it is compared with no-send (0), text-everyone, the optimised response
policy and the *expected* random policy at the same share. This is **internal** validation: these
customers were used during earlier exploration and model development, so it does not replace an
independent test.

**Historical reused test** (`ute historical`). The 2026-10-24 bundle's 40,008-customer test split,
scored twice by the old pipeline (before and after the leakage fix) and once more to freeze its
per-customer scores for corrected intervals, and reused by the audit. Shown for continuity, not as
confirmation.

**Deployment projection** (`ute deploy`). The procedure refitted on all labelled customers and
applied to all 400k. Sums of calibrated effects × assumed margin over the customers actually
texted (holdout excluded) are **model-based scenarios**, not observed campaign results.

**Observed experiment slices**. Raw treated − control differences within bands (age, gender,
tenure, frequency, out-of-fold score decile). Descriptive; slices overlap and are not causal
statements about demographics.

## 5. Uncertainty

All intervals use one bootstrap: customers resampled with replacement **within treatment arm**
(arm sizes fixed, response counts free), 500 replicates, seed 42, percentile 95% intervals. Paired
differences use the same draws, so identical policies differ by exactly 0. "Share of draws > 0" is
a bootstrap fraction, not a probability that a policy is better.

What the intervals include: sampling variability of the evaluation customers for **fixed** scores
and selections. What they exclude: model fitting, calibration fitting and selection uncertainty
(the outer folds share training customers, so fold-to-fold spread is shown descriptively, not
treated as independent replications), and the optimism of choosing a depth from a curve (curve
bands are pointwise). A seeded repeated-experiment simulation
(`docs/evidence/bootstrap_coverage_diagnostic.json`) checks the coverage of the ATE and fixed-policy
intervals against known truths, with Monte Carlo standard errors.

Metric definitions match scikit-uplift (`qini_auc_score(negative_effect=True)`,
`uplift_at_k(strategy="overall")`); committed reference cases generated by scikit-uplift 0.5.1
(ties, negative effect, unequal arms) are checked in the tests.

## 6. Calibration transfer

The calibrator is fitted on out-of-fold scores but applied to a refitted model. For each outer fold
the assessment records the KS distance between those two score distributions and the eligible
share under each, and the pooled reliability table compares calibrated predictions with observed
uplift on validation customers.

## 7. Explainability and monitoring

- Attribution is of the **raw uplift score**: a LightGBM surrogate is fitted on 70% of a sample and
  its fidelity (R², RMSE, error by score decile) is measured on the held-out 30%, where TreeSHAP is
  computed. For an S-learner champion, exact treatment-on minus treatment-off TreeSHAP is also
  computed on the log-odds scale (additive there), with its additivity error recorded. SHAP
  explains the model, not causes.
- Balance: numeric SMD on non-missing values, SMD per category level, and SMD of every feature's
  missingness. A diagnostic consistent with random assignment, not proof.
- PSI compares experiment and non-experiment customers in the same snapshot: a population
  diagnostic, not temporal monitoring, not a deployment gate.
- Predicted groups (persuadable, sure thing, lost cause, do-not-disturb) are business labels from
  model outputs, not identified individual counterfactual types.

## 8. Reproducibility

Every run records git commit + dirty flag, a source-tree fingerprint, config hash, verified raw
dataset fingerprint, feature build, environment fingerprint, seed and parents (`meta.runs`). Model
outputs are history tables keyed by run id. A release is activated only after validation (all
stages succeeded with matching config/data/feature fingerprints and all files present); activation
replaces `releases/ACTIVE` atomically and swaps the dashboard export; failures leave the previous
release active. The pre-remediation results are preserved in a hash-manifested snapshot
(`docs/evidence/2026-10-25_pre_remediation_manifest.json`).
