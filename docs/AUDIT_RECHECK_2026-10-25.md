# Independent remediation recheck

Reviewed commit `c45e661` on 2026-10-25, including the pasted completion summary,
`AUDIT_REMEDIATION.md`, implementation, current release, and tests. No production source, model,
campaign assignment or active-release pointer was changed by this recheck. Failure injection used
a temporary directory and database.

## Verdict

Substantial remediation is real. The outcome bootstrap is corrected, the complete learning and
calibration procedure is used within outer validation folds, future purchase inputs are guarded,
age bands and literal client search are repaired, and API economic eligibility is separated from
campaign authorization. The improved documentation is much more candid about retrospective
validation and fixed-score uncertainty.

However, “all 13 findings addressed” is too broad as a completion claim. A3/A5/A10 still have
reproducible defects or important acceptance gaps. I would fix the five issues below before
calling this finished. They do not establish that the reported ranking signal is false.

## Checks independently repeated

| Check | Result |
|---|---|
| `pytest --cov --cov-report=term-missing` | **99 passed**, **93%** branch-aware coverage, 83.26 seconds |
| Ruff lint and format checks | Passed; 57 Python files formatted |
| `dbt test` against the actual warehouse, current cutoff and release | **61 passed**, no warnings/errors; this is a test run, not a repeat full build |
| All files in current release manifest | All **36** stored hashes verified |
| Manifest config vs current config | Matches |
| Feature-code and dataset fingerprints vs current build | Match |
| Current installed package versions | Match the report: NumPy 2.5.4, pandas 3.1.0, DuckDB 2.0.0, LightGBM 4.7.0, sklearn 1.9.1, Streamlit 1.66.1, Plotly 7.1.0 |
| API lookup, labeled customer | HTTP 200 |
| API lookup, unlabeled customer | **HTTP 500**, reproduced below |
| Fault-injected activation and lineage probes | Remaining defects reproduced, below |

I did not repeat the approximately hour-long full training run, minimum-dependency installation,
fresh clone, browser run, Docker deployment, or coverage simulation. Their committed evidence and
implementation were inspected; those executions remain Claude's evidence, not independently
rerun checks in this pass. Nothing was pushed.

## Remaining findings

### R1 — P1: unlabeled customer lookup returns HTTP 500

**Location:** `src/uplift_engine/api.py:181–193`, especially the conversion at line 190.

`GET /clients/000012768d` succeeds for a labeled customer.
`GET /clients/000048b7a6` fails for an unlabeled customer in active release
`rel-20261025T191554`. The latter has `oof_decile = pd.NA`. The response converts NumPy
scalars but leaves pandas missing-value scalars untouched, which FastAPI cannot serialize.

There are **200,123 rows with missing OOF decile out of 400,162 scored customers**. This is the
normal unlabeled half of the customer population, not an obscure malformed input. Existing API
tests check scoring/authorization but do not exercise the lookup endpoint.

**Claude must:** define a response model/serialization boundary that converts nullable values to
JSON null and handles all supported scalar types. Add lookup tests for labeled and unlabeled
customers, other nullable fields, and unknown IDs. Expected statuses: 200, 200, and 404.

### R2 — P1: activation failure can switch the API and break the dashboard

**Location:** `src/uplift_engine/release.py:154–202`.

`ACTIVE` is replaced at lines 186–190 **before** the dashboard export is renamed at 193–194.
Injecting `OSError` into the final `marts.next.rename(marts)` produces:

```text
activation error: injected mart swap failure
after failure: API active bad; dashboard marts exists False; old backup exists True
after restore: API active bad; dashboard release good
```

`restore_marts` restores the old dashboard directory but does not reconcile the already-switched
API pointer. Database status updates happen afterward and can be stale as well. There is also a
window where dashboard requests see no marts directory during a normal activation.

The existing incomplete-release test covers validation failure before activation. It does not
cover an I/O failure after the pointer has changed. “Atomic activation” is therefore not true
across consumers.

**Claude must:** have API and dashboard resolve one committed release identity to immutable
release contents; keep the committed mart snapshot an export rather than a second authority if
necessary. If retaining a multi-step protocol, implement rollback/recovery with a durable journal
and a clearly documented commit point. Fault-inject each write/rename/database boundary and
verify recovery leaves every consumer on one complete release. Merely moving the pointer write
to the end reverses the mismatch window; it does not by itself create shared atomic visibility.

### R3 — P1: release validation does not verify dependency generations

**Location:** `release.py:79–137`, `lineage.py:158–203`, `cli.py:marts`, `results.py`.

`stage_runs` reads only status, config hash, data fingerprint and feature-build ID. It ignores
source/environment fingerprints and parent IDs. Changing an `explain` run's source/environment
to another generation still passes `release.validate`.

More seriously, after a complete building release has exported its marts/results, a new
successful deploy run with the same config/data/features is accepted alongside the **old** marts,
results and explain run. The returned manifest lists the new deploy run without checking whether
those files were generated from it. File hashes merely fingerprint what exists at activation;
they do not prove parentage or semantic agreement. Null fingerprints are also skipped by the
truthiness filter instead of rejected.

The actual active release has three distinct source fingerprints and two environment
fingerprints across its stages. That alone does not prove its model code changed: source hashes
also include packaging/lock changes, which the report discusses. But the validator cannot
distinguish benign packaging edits from an incompatible model implementation. Every current
stage's `parent_run_ids` is `[]`.

**Claude must:** record exact producer and parent run IDs in each artifact, preserve the
environment/source payload behind its fingerprint, enforce an appropriate computational-code
compatibility policy, and reject missing required lineage. Verify bundle, policy, assignment,
results and marts identify the same release/policy/producer generation. Rerunning an upstream
stage must invalidate downstream outputs until rebuilt. A test must change a producer after
exporting results and show activation fails until its dependents are regenerated.

### R4 — P1: stale feature builds are recorded but not rejected

**Location:** `lineage.py:current_feature_build`, `Run.__enter__`, `data.py:load_features`,
`cli.py:transform` and pipeline stage entry points.

`Run.__enter__` obtains the latest feature-build row and copies its IDs. It never compares its
recorded `as_of`, feature SQL fingerprint or schema fingerprint with the current configuration,
source and warehouse. Neither feature loader adds those checks.

A temporary database containing a feature-build row with cutoff **2000-01-01** is accepted by a
run configured for **2019-03-19**. The lineage records the current config and the stale feature
build side by side without an error. This matters for supported individual-stage commands: a
user changes cutoff/SQL and runs `ute deploy` without `ute transform`.

A failed transform is another risk: dbt can replace some staging/features tables before a later
test fails, while the previous successful feature-build metadata remains. Subsequent stages
could then consume a partly replaced workspace under the old build ID.

**Claude must:** validate the current feature build against current cutoff, feature code, dataset
and schema before assessment/deployment/explanation/marts. Ensure a failed transform cannot leave
a partially modified workspace eligible for modeling under prior metadata; invalidate before
building or use isolated build schemas/files and promote only after success. Test changed
cutoff, changed SQL, changed raw ingest and a failure partway through transform.

### R5 — P2: a normal fresh clone cannot complete the advertised pipeline

**Location:** `release.py:REQUIRED_STAGES`, `cli.py:run`, `pipeline.py:historical`,
`results.py:build_results`, README quickstart.

`historical` is mandatory for release activation and requires a gitignored local snapshot.
`results.py` and dashboards also assume that historical results exist. Thus even after a fresh
clone downloads and processes the full public dataset, the default `ute run` fails at historical
recalculation and cannot activate its freshly trained model. This happens after expensive
assessment and deployment stages. The completion report discloses the limitation, but disclosure
does not make the project reproducible for another person.

The “fresh clone: 99 passed” check supplies an artificial historical snapshot in its raw fixture;
it does not establish that the public-data quickstart works from a clean clone.

**Claude must:** make historical comparison an optional, explicitly unavailable evidence section
when the old snapshot is absent, or distribute/reproduce the required frozen comparison artifact
appropriately. New assessment/deployment must be able to form a complete release without the
auditor's old local files. Add a fixture integration run with no historical snapshot and require
successful release/API/dashboard behavior, with historical evidence visibly unavailable. Do not
invent or silently substitute historical numbers. Preflight any genuinely required external
artifacts before costly training.

## Additional validation gaps and interpretive caution

1. **The no-leakage regression test does not test leakage.**
   `tests/test_integration.py:98–105`, named
   `test_assessment_never_fits_on_validation_labels`, only checks row counts and fold values.
   Those assertions would pass even if validation labels were accidentally supplied to a fit.
   The current implementation appears properly separated on inspection, but the cited test
   does not establish that property. Spy on training/calibration inputs and assert customer
   disjointness; with fold assignments fixed, perturb validation labels and verify fitted
   artifacts for that fold are unchanged.

2. **The browser checker can accept empty traces.** Its bad-trace predicate rejects only
   `n > 0 and finite == 0`; a trace with `n == 0` is allowed. Missing hover targets return None
   and are filtered out of `all(...)`, so an all-empty hover list can pass. It also requires
   only some plots, not all expected plot identities/counts. The existing saved evidence shows
   six charts with tooltips, so this is not a claim that today's HTML is blank. Strengthen the
   regression oracle to reject empty required data traces and missing required chart IDs, while
   explicitly exempting decorative traces. Add failing fixtures for missing/empty charts.

3. **Full refit threshold instability remains worth investigating.** Outer validation selects
   33.67%. Full-refit eligibility is **26.64% on the same labeled customer population** and
   **26.85% on the unlabeled population**. Therefore the gap cannot mainly be explained by
   including the unlabeled half of the base. Isolate training-size, champion choice, calibration
   and OOF-to-refit effects, and quantify sensitivity around break-even. This is a documented
   generalization limitation, not proof that the projected value is wrong. The new profit
   intervals remain retrospective fixed-score diagnostics and do not validate a future campaign.

## Claude follow-up order and completion requirements

1. Fix R1 and add actual lookup endpoint coverage.
2. Fix the shared activation/recovery design (R2), then producer/dependent validation (R3).
3. Enforce feature-build freshness and failed-build isolation (R4).
4. Remove the mandatory private-snapshot dependency from ordinary releases (R5).
5. Strengthen the no-leakage/browser regression assertions and revise the refit-gap explanation.
6. Update `AUDIT_REMEDIATION.md` honestly: do not mark A3/A5/A10 fully closed until these new
   regressions pass. Preserve this recheck and the earlier audit.
7. Return exact regression results, a clean no-snapshot integration run, and evidence that
   release identity is consistent across API, campaign, results and dashboard after both success
   and injected failure. Rebuild the real release only after the corrected validation design is
   settled; do not retrain repeatedly seeking improved metrics.

No push/deployment was requested by this recheck. This is a further verification pass, not an
instruction to discard the substantial improvements already implemented.
