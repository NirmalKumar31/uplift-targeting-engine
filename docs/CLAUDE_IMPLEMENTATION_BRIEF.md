# Claude: implement the audit remediation end to end

Work in `/Users/nirmalkumar/Projects/uplift-targeting-engine`.

Read `docs/AUDIT_2026-10-25.md` in full first. It contains verified findings A1–A13, evidence, scope limitations and the original audit verdict. This brief translates those findings into implementation work. The user wants a defensible, maintainable portfolio project, with honest causal evaluation and a coherent working dashboard/API—not a higher number obtained by inspecting test results.

Implement the necessary fixes, run appropriate checks, and produce a finding-by-finding completion report. Do not stop at a plan. Preserve unrelated user changes. Keep changes reviewable and organized by concern. Do not publish or push merely because this brief asks for local fixes.

## 0. Preserve evidence and correct the claims first

Before rebuilding, preserve the existing model selection, result tables, calibration tables, scores, run logs and test-access history in a versioned local audit snapshot with a manifest. Do not put raw data or large model binaries in git. Record the snapshot path and hashes. Keep the original audit unchanged; write remediation outcomes separately.

Correct prominent README, methodology and static-site wording immediately:

- The original test was evaluated twice, including a leakage correction, and later reused in this audit. It is not pristine or literally evaluated once.
- Qini +0.0164 and the roughly 2× profit comparison are historical point estimates under stated assumptions.
- The original profit uncertainty is defective; do not keep presenting it as valid while replacing the method.
- The production calibrated send rule has not yet been evaluated independently as a complete policy.
- Full-population profit and extra buyers are modeled projections, not observed campaign results.
- R² 0.98 was training surrogate fit, not 98% accuracy or held-out fidelity.

The audit's arm-bootstrap paired advantage interval was approximately −₽240 to +₽1,253 per 1,000. Preserve it as an audit diagnostic of the existing policy, not a new confirmatory result. Do not repeat test evaluation until the new protocol is written down and frozen.

**Done when:** README, generated site wording, methodology and dashboard labels agree about evaluation history and the status of the evidence. Existing results remain recoverable.

## 1. Repair inference and metric contracts — A1

Primary files: `evaluation/metrics.py`, `policy.py`, ranking-report helpers, metric/policy tests.

1. Replace treatment × outcome bootstrap cells with customer resampling within treatment arm for the ordinary fixed-model uncertainty calculation. Treatment-arm sample sizes may be fixed; response counts must vary.
2. Reuse the same customer draws for paired model and policy differences. Preserve score/outcome/customer correspondence. Where repeated rows per customer exist, resample at customer level rather than transaction level.
3. Expose uncertainty settings consistently. The current profit curve defaults to 200 draws even when experiment config requests 500. Record seed, replicate count, resampling unit and method in results.
4. Publish profit differences versus blanket, no-send and response policies with intervals. Do not use a ratio as the primary uncertainty summary when its denominator is noisy or can cross zero.
5. Rename `p_champion_better` and UI wording to describe the bootstrap fraction of positive differences. Do not present it as a posterior probability or an ordinary p-value.
6. Label intervals by what they include: a fixed-score evaluation bootstrap does not include model fitting, calibration fitting or selection uncertainty. Pointwise curve bands are not simultaneous coverage of a searched optimum.
7. Validate equal lengths, nonempty input, binary treatment/outcome, finite scores, valid k/depths, required treatment-arm support and degenerate Qini normalization. Use explicit errors or a documented undefined-result contract. Never silently render invalid metrics as meaningful zeroes.
8. Preserve reproducible Qini reference cases against the official definition, covering tied scores, negative effects and unequal arm sizes. Keep a comparison script or immutable reference fixtures with provenance; a verbal 10-decimal claim is insufficient.

**Required evidence:** nonzero blanket/ATE interval in a nondegenerate Bernoulli experiment; paired identical-policy difference exactly zero; sensible behavior under a known null and known heterogeneous effect; a seeded repeated-experiment coverage diagnostic whose finite-simulation uncertainty is reported. Do not create brittle tests that require an exact simulated coverage percentage or a particular favorable model ranking.

## 2. Evaluate the whole decision policy — A2, A13

Primary files: `pipeline.py`, `models/learners.py`, policy/calibration marts, evaluation documentation.

Write down the target population, binary outcome horizon, treatment definition and assumed utility `margin × incremental buyer − SMS cost`. State that constant historical basket × gross margin is an assumed utility and does not identify causal incremental revenue or transaction counts.

Implement an outer-validation assessment of the complete policy:

1. Reserve outer validation customers.
2. On outer-training customers only, perform any model/member/depth selection. Generate inner OOF scores to fit the calibrator. If the champion is fixed in advance for this assessment, explicitly record that choice and its previous exploratory origin.
3. Fit the learner on outer-training customers. Apply its predictions and the outer-training-only calibrator to outer validation. Never fit the calibrator using the outer validation outcomes.
4. Define the exact deployed action as calibrated effect exceeding cost/margin, with explicit equality semantics. Evaluate that rule on outer validation. Report its selected share and arm counts as well as policy value.
5. Check the OOF-to-refit score distribution/calibration transfer rather than assuming it. If it is unreliable, change the deployment construction or calibrate on a separate appropriate sample.
6. Evaluate observed treatment/control outcomes using a stated randomized-experiment policy-value estimator. Difference in means within a fixed selected subgroup is understandable; a known-propensity IPW/DR formulation is also acceptable with its assumptions stated. Include correct population scaling and paired comparisons.
7. Evaluate each comparator fairly. A response policy described as optimized must be selected on training/development data, not forced to use the uplift policy's depth. Include no-send, blanket and the expected random policy value; distinguish one realized random subset from that expectation.
8. Aggregate outer-fold evaluation with a defensible uncertainty method. Do not treat overlapping training folds as independent experiments or claim a row bootstrap includes training uncertainty. If training-inclusive inference is out of scope, state the limitation clearly.

Historical test reuse cannot be undone. A fresh random split of the same inspected customers is internal validation, not independent confirmation. Without new data, present the improved assessment as retrospective/internal validation. Do not claim a new unseen final test merely by changing the seed.

**Done when:** evaluation produces an identifiable policy bundle including learner, calibrator, economics and action semantics; assessment measures that same procedure; the dashboard separates outer validation, historical reused-test results, and full-data deployment projections.

## 3. Add actual point-in-time feature safety — A4, A8

Primary files: dbt staging/features models, YAML tests, reference-date config.

1. Establish one explicit configured as-of timestamp and document its provenance. Do not invent an exact SMS timestamp from max purchase time alone.
2. Create a shared purchase input with `transaction_datetime < as_of`. Use it for every purchase feature, including product mix currently reading raw purchases directly. Decide whether invalid future rows fail ingestion/build or are excluded with an explicit quality count; do not hide the behavior.
3. Define rolling windows precisely, including start/end boundaries and timezone assumptions. A future timestamp must not qualify because `days_ago <= N`.
4. Validate transaction-level repeated fields before using `any_value`; contradictory basket totals/store/timestamps must fail or follow an explicit reconciliation rule.
5. Add chronology checks for issue/redeem dates and handle negative tenure/redemption intervals. Keep unknown values unknown rather than inventing plausible replacements.
6. Correct age bands. Ages 14–17 must never be labeled `18-24`. Document invalid-age and missing-age handling.
7. Validate outcome/treatment nulls and binary values, client/product references, key uniqueness and row-grain assumptions needed by joins.

**Required evidence:** run the real dbt build on small raw fixtures containing a future transaction, a future redemption, contradictory transaction fields, missing dates, unmatched keys and a 16-year-old customer. Assert both the resulting features and expected build failures. A post-cleanup test that can never see the prohibited raw value is not the only protection.

## 4. Make results reproducible and releases consistent — A3

Primary files: `config.py`, pipeline logging, artifact writing/loading, warehouse outputs.

1. Persist split assignments and a dataset/split fingerprint. Reject accidental reuse of selection/model artifacts with a different dataset, as-of time, feature schema or config.
2. Capture source revision and dirty status. If no git revision is available, preserve a source-content fingerprint rather than relying on `unknown` for lineage.
3. Record actual verified raw hashes, feature SQL/config hash, environment/lock fingerprint, model/calibrator hashes, economics, seed and parent run IDs.
4. Preserve evaluation results by run ID rather than replacing the only copy. Test-access entries should reference the evaluated bundle and reason for access. A documented explicit override is appropriate for a repeated evaluation; an audit log is not a security guarantee against arbitrary Python reads.
5. Write a complete release into a versioned staging location and activate it only after validation succeeds. Define transaction/atomic activation boundaries for model bundles, scores and mart exports. A failed stage must leave the previously active release usable.
6. Make model and dashboard cache invalidation depend on release identity or fingerprints. Document activation/restart semantics.

**Required evidence:** mismatched artifacts fail clearly; a simulated incomplete release does not become active; a changed mart release becomes visible without a manual mystery cache reset; old evaluation history remains queryable.

## 5. Give scoring and campaign decisions explicit contracts — A5

Primary files: `api.py`, feature preparation, batch action assignment, API tests.

1. Define a reusable feature schema shared by serving and training transformations. Check types, finite numerics, category values, meaningful ranges and proportions. Distinguish omitted, null and invalid fields deliberately.
2. Decide and document minimum viable input. Reject `{}` and similarly unusable records with 422. Missing optional inputs can remain supported; include completeness/quality information if partial rows are allowed. Unknown gender must not silently become a high-confidence prospect.
3. Keep pure score/eligibility separate from campaign send authorization. If returning `send`, state exactly what it authorizes. An actual campaign action endpoint must enforce eligibility, selected policy and holdout.
4. Use persistent campaign ID + customer ID assignment (for example a documented deterministic hash rule), not RNG position in a changing dataframe. Persist policy version, assignment and assignment probability.
5. Keep holdout customers excluded from send exports and authorization paths. Reordering or adding other customers must not change existing assignments for the same frozen campaign.
6. Handle missing/incompatible model bundles and absent warehouses with deliberate service errors. Separate liveness from model readiness. Define model activation/cache behavior.
7. Verify batch/API score parity for the same full feature row. Test a known holdout, malformed values, unknown categories, nonfinite values, empty rows, oversized/empty batches and missing artifacts.

This project need not become an actual SMS delivery service. Do not add a live provider integration. It needs a safe and unambiguous demonstration of recommendation versus campaign assignment.

## 6. Fix dashboard and static-site defects — A6–A8, A13

1. Use literal client substring search (`regex=False` with explicit missing-value handling). Verify `[`, `(` and `.` are harmless literal inputs.
2. Match Plotly's generated serialization with its JS renderer. Remove the unrelated hard-coded JS 2.35.2 version; use the matching bundled/CDN inclusion emitted by the installed Plotly package. Include the library once.
3. Perform browser checks on the generated HTML: expected plots/traces contain real numeric points, hover works, no relevant console exceptions, and narrow screens remain usable. Python page execution does not substitute for this.
4. Recompute the sensitivity table from the current simulator settings. Make holdout handling explicit and consistent with the displayed send count/profit.
5. Label population and evaluation source for every result. A training/all-label OOF curve, reused test result, calibrated production projection and observed experiment slice must not appear interchangeable.
6. Never label the optimized noisy curve maximum as proven optimal profit. Display the estimated optimum with its selection/uncertainty limitations and the no-send alternative.
7. Correct age bands across SQL, charts, summaries and exports. Show all relevant age groups truthfully.
8. Mark model-based extra-buyer/profit sums as conditional forecasts under configured assumptions. Aggregating a holdout/skip view must not imply those customers were texted.

**Required evidence:** all seven page scripts plus interaction checks pass; changing margin changes all dependent outputs; exports respect action; the static site renders actual traces using the generated file. Document browser validation limitations if tooling is unavailable instead of marking it done.

## 7. Explainability and monitoring — A9, A12

1. Split surrogate-fitting and fidelity-evaluation rows. Report held-out R² and an error measure, plus error by uplift region when sample support permits. Show training fit separately if retained.
2. For the S-learner, investigate direct treatment-on minus treatment-off TreeSHAP contributions if exact additivity and feature semantics can be verified. Do not substitute an unverified explanation method merely to remove the surrogate.
3. Describe raw-score attribution as raw-score attribution. Do not claim additive explanation of an isotonic-calibrated effect/profit unless explicitly implemented and verified.
4. Measure categorical balance per category and missingness balance. Keep numeric SMD, but replace “randomization proved” with an appropriate measured-balance statement and the documented assignment assumption.
5. Label current PSI as experiment-versus-unlabeled historical population comparison. It is not temporal monitoring of a new campaign. If status is called a deployment gate, enforce the gate with a documented override; otherwise call it a diagnostic.
6. Explain that Persuadable/Sure thing/Lost cause/Do not disturb are prediction-based business labels, not identified individual counterfactual types. Include support and uncertainty for demographic comparisons rather than asserting age causes responsiveness.

## 8. Test the actual deliverable — A10, A11

Build a small, deterministic raw dataset with enough treated/control buyers/nonbuyers for the configured folds. Include compressed CSVs in the real ingestion schema, line items repeating basket totals, product records and unlabeled customers.

Run an integration path through the actual entry points:

`ingest → transform/dbt build → train → evaluate → deploy → explain → marts → API/dashboard/static smoke`

Use local fixture input/checksum manifests so CI does not download the full dataset or require network access. Do not alter real-source checksums to make fixtures pass. Configure tiny models/bootstrap counts only for integration speed; retain separate statistical diagnostics.

Test the distribution contract explicitly:

- Choose supported source-checkout and/or installed-wheel behavior. If supporting the wheel, bundle/load config and dbt resources correctly and run from outside the repository. Do not derive repository paths from a site-packages layout.
- Align dependency minimums with APIs actually used, including NumPy `trapezoid` and recent Streamlit controls/layout arguments. Test minimums or narrow the advertised support.
- Use a compatible, pinned/tested uv toolchain and locked CI installation. Docker execution is currently unverified, not known broken solely because the daemon was absent.
- Add `.dockerignore`. Document whether the API requires an independently generated model/warehouse, and validate missing mounts/artifacts clearly. Test dashboard-only startup from committed marts and API startup with fixture artifacts.
- Validate only accepted configurations: positive margin, sensible folds, nonempty model candidates and ensemble membership/size. Remove or implement silently ignored tuning switches. Centralize the as-of setting or document the separate config boundary honestly.

## 9. Regenerate results once the implementation is ready

Run the final documented retrospective protocol and refresh the dependent warehouse tables, parquet marts, dashboard snapshot and README from a consistent release. Preserve old results under their original run IDs.

Do not hard-code favorable numbers into presentation files. Generate a compact results artifact with policy ID, evaluation source, population, sample size, selected share, economics, ranking metrics, calibration checks, policy value and paired differences/intervals. Both dashboards and written summaries should be traceable to it.

Do not add a complicated model search merely to restore historical numbers after corrections. A lower honest estimate is an acceptable outcome. If a claim is no longer supported, remove it.

## 10. Final response required from Claude

Create `docs/AUDIT_REMEDIATION.md` containing:

| Finding | Status | Files changed | Evidence/check | Remaining limitation |
|---|---|---|---|---|
| A1–A13 individually | Fixed / Partially fixed / Deferred / Disputed | Exact locations | Actual result | Specific and honest |

For disputed findings, supply a reproducible counterexample or reference and explain why it changes the conclusion. For deferred findings, give a concrete reason and ensure the public wording does not imply the missing capability exists.

Your final message must state:

1. What changed and why.
2. Which checks actually ran, with results and relevant versions.
3. Revised metrics and uncertainty, clearly distinguishing exploratory/internal evidence from independent confirmation.
4. Whether batch/API/holdout semantics agree.
5. Whether a clean checkout, wheel (if supported), Docker and browser were actually exercised.
6. Any blockers, remaining risks and exact commands for reproducing the verified release.

No claim of “everything fixed” based only on 32 passing tests, no fabricated browser/container validation, and no claim that a new split erases prior test reuse. Completion means the behavior, evidence and public story agree.
