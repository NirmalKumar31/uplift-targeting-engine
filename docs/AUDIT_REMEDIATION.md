# Audit remediation report

Response to [AUDIT_2026-10-25.md](AUDIT_2026-10-25.md) (findings A1–A13) and to the follow-up
[AUDIT_RECHECK_2026-10-25.md](AUDIT_RECHECK_2026-10-25.md) (findings R1–R5 plus three validation
gaps). Both documents are unchanged. The work followed
[CLAUDE_IMPLEMENTATION_BRIEF.md](CLAUDE_IMPLEMENTATION_BRIEF.md). Everything below was run
locally on 2026-10-25 (macOS arm64, CPython 3.12, uv 0.13.5). Nothing was pushed.

**Releases.**
- Pre-remediation outputs are frozen in `snapshots/2026-10-25_pre_remediation/`, gitignored, with
  hashes in
  [evidence/2026-10-25_pre_remediation_manifest.json](evidence/2026-10-25_pre_remediation_manifest.json).
- The **active release** is `rel-20261025T222701743564` (policy `e51b7c065a512ebc`). It was
  rebuilt once, after the recheck fixes, from a clean tree. All five of its stages share one code
  fingerprint, one environment fingerprint, one config hash, one dataset fingerprint and one
  feature build, and their parent runs are recorded.
- The previous release `rel-20261025T191554` (policy `06c39b17c1e9d923`) is superseded. Its runs
  and results remain in the warehouse history; it had three different code fingerprints, which the
  old validator did not detect (recheck R3).

## Recheck findings (R1–R5)

| Finding | Status | Files changed | Evidence / check | Remaining limitation |
|---|---|---|---|---|
| **R1** unlabelled client lookup → HTTP 500 | **Fixed** | `api.py` (`jsonable` serialization boundary), `tests/test_integration.py` | Every missing marker (pd.NA, NaN, NaT, ±inf) becomes JSON null; NumPy scalars become Python scalars. Tests: labelled 200, unlabelled 200 with `oof_decile: null`, unknown 404, plus a unit test of every marker. The recheck's IDs on the real release: `000012768d` → 200, `000048b7a6` → 200 (null), `nope` → 404. Same results in the Docker API container. | — |
| **R2** activation could split API and dashboard | **Fixed** | `release.py`, `dashboard/common.py`, `config.py` (`UTE_CONFIG`), `cli.py`, `tests/test_release_integrity.py` | The only commit point is one fsync'd `os.replace` of `releases/ACTIVE`. The release directory, including its marts, `RELEASE.json` and manifest, is complete before the commit and immutable after it. **The API and the dashboard both resolve that pointer** to the same directory, so they switch together. The `data/marts` export and `meta.releases` statuses are derived state, re-derived by `reconcile` (`ute status`/`release`/`run`); `releases/JOURNAL.json` records phases. Fault injection at the `RELEASE.json` write, the manifest write and the pointer write leaves **all** consumers (API `/ready`, dashboard resolver, `results.json`, export) on the old release. A fault in the post-commit export leaves API, dashboard and results on the new release, with only the export copy lagging until `reconcile` repairs it. An interrupted export swap is also repaired, and the dashboard keeps serving through it. | The export copy can lag the pointer after a crash until the next `ute status`/`release`/`run`. A dashboard running without access to `releases/` (the dashboard-only Docker image) serves the committed export by design. |
| **R3** inconsistent generations accepted | **Fixed** | `lineage.py`, `release.py`, `pipeline.py`, `cli.py` (`marts`), `results.py`, `dbt/macros/release_run.sql`, tests | Runs record exact parents: explain → deploy; marts → assess, deploy, explain and historical. The code and environment payloads behind each fingerprint are stored in `meta.fingerprint_payloads`. Policy: every run in a release must have non-null, **identical** code (`COMPUTE_GLOBS`), environment, config, dataset and feature-build lineage. Artifacts carry `producer_run_id`; marts are built from explicit producer vars and write `PRODUCERS.json`. Validation checks the bundle's deploy run, every parquet's producer, `PRODUCERS.json`, both `results.json` copies and `dim_client` all agree on runs and policy version. Tests: rerunning deploy after marts is **rejected** until explain and marts are rebuilt; a changed or missing code/env/config fingerprint is rejected (this caught a real bug, NULL read back as NaN and accepted); a scores file from another producer is rejected. | The compatibility policy is strict equality: any computational code or lock change requires rebuilding the whole release. There is no semantic "compatible change" classification. |
| **R4** stale feature builds accepted | **Fixed** | `lineage.py` (`begin/finish_feature_build`, `require_fresh_features`), `cli.py` (`transform`), `pipeline.py`, `tests/test_freshness_and_snapshot.py` | Builds are recorded as `building` **before** dbt touches the workspace, and become `succeeded` only after the build and tests pass, otherwise `failed`. Every modelling stage requires the latest build to have succeeded and still match the configured cutoff, current feature SQL/macros, the latest verified raw ingest and the feature-table schema. Tests refuse each case: changed cutoff, changed feature SQL, re-ingested raw data, and a transform that fails partway through (contradictory raw data). | The check covers the latest build. Deliberately reusing an older build is not supported. |
| **R5** fresh clone cannot complete | **Fixed** | `pipeline.historical_available`, `cli.py` (`run` preflight, `historical`), `release.py` (historical optional), `results.py`, dbt historical marts (`enabled=var('has_historical')`), dashboard overview, `build_site.py`, README renderer | Historical comparison is optional and preflighted before any expensive stage. Without the snapshot, `ute run` builds a complete release and records `historical_test: {available: false, reason}`. The dashboard, site and README say "unavailable" and substitute nothing. End-to-end test with no snapshot: release active, API ready, all 7 dashboard pages without exceptions, site built without the historical chart. `ute historical` alone fails clearly without the snapshot. | Others cannot reproduce the historical comparison without the frozen snapshot, which is not distributed: it is derived from the full public dataset and stays local. |
| Gap 1: leakage test did not test leakage | **Fixed** | `tests/test_integration.py` | The test spies on every outer-fold fit and asserts no validation customer is among its training rows. With folds fixed, it flips fold-0 validation labels and requires fold 0's champion, calibrator bins, response share and predictions to be **bit-identical**; fold 1 must change (positive control). Mutation-checked: making assess fit on all customers fails the test ("fold 0 trained on validation customers"). | — |
| Gap 2: browser oracle accepted empty traces | **Fixed** | `scripts/browser_check.py`, `scripts/build_site.py`, `tests/test_browser_check.py` | Required chart ids are decided from `results.json`, not from the page. Every non-decorative trace needs finite numbers on its value axis. Every required chart needs a working hover. Only traces tagged `meta="decorative"` are exempt. A real-Chrome test proves it fails a page with an empty chart and missing charts and passes a valid horizontal bar chart. (Running it on the real site first produced false positives on horizontal bars; the checker now reads the value axis.) | — |
| Gap 3: refit vs validation selection share | **Investigated** | `scripts/refit_gap_analysis.py`, [evidence/refit_gap_analysis.json](evidence/refit_gap_analysis.json) | **Not** OOF-to-refit transfer: on all labelled customers the deployed calibrator selects 26.0% on the champion's own OOF scores and 25.7% on the refit's scores (KS 0.020); unlabelled 25.9%. The cause is the **isotonic step at break-even**: calibrated uplift jumps from 2.50% (bins 13–14, about 10% of customers) to 3.78% (bin 15) across the 2.85% break-even. Whether those bins clear it depends on their noisy observed uplift (10k customers per bin, SE ≈ 0.65 pp), so separately fitted calibrators put the cut in different places: outer folds select 28.5–44.4%, the full refit 25.7%. At 0.75× break-even the share jumps to ~59%. | The selected share, and hence the projection, is **unstable** near break-even. The validated policy value (34.1% pooled) averages over that instability; the full-data refit is one draw from it and is not separately validated. A smoother calibrator (more bins, or a parametric fit) or a fixed-share rule would reduce this. Not changed here, to avoid tuning after seeing results. |

## Original findings (A1–A13), revised statuses

| Finding | Status | Files changed | Evidence / check | Remaining limitation |
|---|---|---|---|---|
| **A1** bootstrap froze response counts | **Fixed** | `evaluation/metrics.py`, `policy.py`, `evaluation/diagnostics.py`, metric/policy tests, `tests/fixtures/qini_reference.json`, `scripts/make_qini_reference.py`, `scripts/coverage_diagnostic.py` | Customer-within-arm bootstrap with shared draws. The real blanket interval is now ₽72–₽933 (was 499.86–499.86). An identical-policy paired difference is exactly 0. Coverage over 200 simulations × 2 scenarios: ATE 0.970/0.960, policy value 0.965/0.945, MC SE ≈ 0.013 ([evidence](evidence/bootstrap_coverage_diagnostic.json)). Four sklift 0.5.1 reference cases match to 1e-12. | Fixed-score intervals: they exclude fitting, calibration and selection uncertainty. Percentile method. |
| **A2** deployed rule never evaluated as a policy | **Fixed** (internal-validation limits) | `pipeline.py`, `policy.py`, `results.py`, assessment marts | 5 outer folds of the whole procedure. Leakage is now *tested* (Gap 1). Pooled over 200,039 customers, the deployed policy texts **34.1%**: **₽1,190 [917, 1,443]** per 1,000; vs text-everyone **+₽690 [+328, +1,050]**; vs the optimised response policy (texts 92.8%) +₽777 [+428, +1,139]; expected random at the same share ₽171 [25, 318]. Calibration gap 0.70 pp. Transfer KS ≤ 0.028; eligible share OOF vs validation within 1.3 pp per fold. | Internal validation on customers seen during exploration. Champion by fold: S, S, S, S, X. Selected share unstable near break-even (Gap 3). |
| **A3** "evaluated once" and lineage claims | **Fixed** (claims, lineage, generations); access log is not a lock | `lineage.py`, `release.py`, `warehouse.py`, `cli.py`, evidence scripts, docs | Wording corrected everywhere. Lineage is enforced, not just recorded (R3, R4). History is append-by-run_id. | The test-access log is an audit aid, not a security lock. The audit's own reuse is recorded in the audit. Dirty trees are recorded, not refused. |
| **A4** future-data guard missed purchases | **Fixed** | dbt staging/features/sources, macros, config | Point-in-time input for all purchase features; source tests fail the build on future/null timestamps, contradictory fields, orphan keys and train/test overlap. **Real build 63/63** (this release). Four raw-defect fixtures each fail the real dbt build. | `as_of` is a feature cutoff, not a published send time; no timezone. |
| **A5** unusable records; holdout not enforced | **Fixed** (incl. R1) | `contract.py`, `api.py`, `pipeline.py`, `cli.py` | 422 on unusable/invalid records; eligibility separate from authorization; frozen hash assignment; lookup serialization fixed (R1). Batch/API parity on 200 real customers: max difference 0.0. In the container: holdout denied, send authorized, unlabelled lookup 200. | No live SMS provider. The campaign id should be versioned per release in a real deployment. |
| **A6** Plotly renderer mismatch | **Fixed** | `build_site.py`, `browser_check.py`, CI | plotly.js loaded once from the URL the installed Plotly emits (4.1.1). Strict browser check (Gap 2) on this release: 6/6 required charts with numeric data and hover, 0 console errors, 0 px overflow at desktop and phone width ([evidence](evidence/browser_check.json)). | The published Pages site still shows the old page until pushed; CI has not run on GitHub. |
| **A7** client search crash | **Fixed** | `clients.py` | Literal search; `[`, `(`, `.` raise nothing in AppTest or real Chrome. | — |
| **A8** age band, anomalies | **Fixed** | dbt `dim_client`, `stg_clients`, `feat_demographics`, age-band test | `14-17` band; chronology and invalid ages become unknown and are counted. | No minors-exclusion rule (campaign-owner decision). |
| **A9** surrogate fidelity | **Fixed** | `pipeline.explain`, `drivers.py` | Held-out R² **0.951** (training R² 0.983); exact S-learner log-odds attribution additivity error **7.5e-15**. | Exact attribution is on the log-odds scale; dashboard importance comes from the surrogate. |
| **A10** CI bypassed the data path | **Fixed** locally; CI **unverified on GitHub** | raw fixture, integration, release-integrity and freshness tests, CI, Docker | Raw fixture through the real entry points, including a no-snapshot run. Fault-injection and lineage regressions. Docker (Colima) exercised: dashboard container serves the committed export of this release; API container ready with the release mounted, 503 without one. | The workflow has not run on GitHub runners. Docker was verified on arm64 Colima only. |
| **A11** wheel and version contracts | **Fixed** (source-checkout mode) | `config.py`, `cli.py`, `pyproject.toml`, `uv.lock` | Floors verified by a lowest-direct full-suite run (it found Streamlit ≥1.52, PyYAML ≥6.0.1, Typer ≥0.15.4). Clean error outside a checkout. | Standalone wheel not runnable by design; only Python 3.12 is supported. |
| **A12** monitoring and segment wording | **Fixed** | `monitoring.py`, `health.py`, `segments.py` | Category- and missingness-level balance; PSI labelled a diagnostic; groups labelled predicted. | No temporal monitoring data exists. |
| **A13** economics mixed estimands | **Fixed** | dashboard pages, `results.py`, site, README renderer | Source badges everywhere; simulator recomputed from current controls including holdout; numbers generated from `results.json`. | The simulator curve is the (optimistic) selection-data OOF curve, labelled as such. |

## Lower-priority items from the audit

| Item | Disposition |
|---|---|
| Unused `tuning` switch; `reference_date` outside the YAML | Removed tuning; `as_of` lives only in `configs/pipeline.yaml`. |
| Config validation | Candidates, ensemble size, folds, positive economics and holdout range validated and tested. |
| Unequal treatment and class transformation | Arm-reweighted class transformation (tested at 20% treated). |
| `load_raw` trusted configured hashes | Hashes actual bytes at load; expected and actual digests per `ingest_id`. |
| Non-atomic releases; stale caches | Single pointer commit read by every consumer (R2); caches keyed on release id. |
| Metric validation | Explicit errors or counted NaN for invalid or degenerate inputs; tested. |
| "2.1× prior project" comparison | Removed. |
| Dataset terms vs MIT license | MIT covers code only; dataset terms are the competition's. Granular marts are committed; no legal conclusion drawn. |
| `scripts/audit_profit_uncertainty.py` | Kept verbatim (targets the pre-remediation layout). `ute historical` reproduces its estimand: 41% policy − text everyone **₽514 [−223, +1,271]** vs the audit's [−240, +1,253]. |

## Revised metrics, by evidence type (release `rel-20261025T222701743564`)

1. **Outer-fold retrospective validation** (internal; whole procedure; 500 customer-within-arm
   draws; fixed fitted policies). The deployed policy texts 34.1%: ₽1,190 [917, 1,443] per 1,000.
   Paired vs text-everyone **+₽690 [+328, +1,050]** (share of draws > 0: 1.000); vs the optimised
   response policy +₽777 [+428, +1,139]. Pooled raw-score Qini 0.0147 [0.0118, 0.0175]. Fold
   values ₽772–₽1,324.
2. **Historical reused test** (2026-10-24 bundle; corrected intervals). Qini +0.0164
   [+0.0102, +0.0224]. The 41% policy vs text-everyone is **₽514 [−223, +1,271]**: includes zero.
3. **Deployment projection** (model-based; selected share unstable near break-even, see Gap 3).
   92,933 to text and 10,456 held out of 103,389 eligible; projected 6,264 additional buyers and
   ₽380,881 among those texted, vs ₽200,025 for texting everyone.

None of these is independent confirmation. A future campaign evaluated against its holdout group
would be.

## Checks actually run (final)

| Check | Result |
|---|---|
| `ruff format --check` + `ruff check` | pass |
| `pytest` on the locked env (numpy 2.5.4, pandas 3.1.0, duckdb 2.0.0, LightGBM 4.7.0, sklearn 1.9.1, Streamlit 1.66.1, Plotly 7.1.0, dbt 1.12.6) | **124 passed**, 92% branch-aware coverage |
| `pytest` on lowest-direct dependencies (numpy 2.0.0, pandas 2.2.2, duckdb 1.1.0, Streamlit 1.52.0, Typer 0.15.4, …) | **123 passed, 1 skipped** (the real-Chrome oracle test; Playwright is not installed in that environment) |
| Real `ute run --skip-ingest` from a clean tree | all stages succeeded with one lineage generation; dbt features 63/63, marts 40/40; assess 1,236 s, deploy 586 s |
| Consumer identity on the real release | pointer = API `/ready` = campaign authorization = dashboard resolver = `results.json` = export = `rel-20261025T222701743564`; policy `e51b7c065a512ebc` everywhere |
| Strict real-Chrome check (Chrome 156): site at desktop and phone width, 7 Streamlit pages, literal search | pass ([evidence](evidence/browser_check.json)) |
| Docker (Colima, Docker 29.5.2): rebuilt image; dashboard container export; API ready with the release; lookups 200/200/404 | pass |
| Leakage-test mutation check | a leaky assess fails the test |
| Refit-gap analysis | [evidence/refit_gap_analysis.json](evidence/refit_gap_analysis.json) |

## Reproduce

```bash
uv sync --locked --all-extras --group browser
uv run ute run                     # or --skip-ingest when data/raw is present; historical optional
uv run python scripts/build_site.py && uv run python scripts/render_readme_results.py
uv run pytest
uv run --isolated --resolution lowest-direct --all-extras --group dev pytest
uv run --group browser python scripts/browser_check.py --site docs/index.html --results data/marts/results.json
uv run python scripts/refit_gap_analysis.py
docker compose up --build          # dashboard :8501, API :8000 (mount ./releases)
```

On a fresh clone without the local evidence snapshot, `ute run` still produces a complete,
activated release; the historical comparison is reported as unavailable.
