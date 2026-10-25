# Audit remediation report

Response to [AUDIT_2026-10-25.md](AUDIT_2026-10-25.md) (findings A1–A13; the audit itself is
unchanged). Work followed [CLAUDE_IMPLEMENTATION_BRIEF.md](CLAUDE_IMPLEMENTATION_BRIEF.md).
Everything below was run locally on 2026-10-25 (macOS arm64, CPython 3.12, uv 0.13.5). Nothing
was pushed.

**Releases.** Pre-remediation outputs are frozen in `snapshots/2026-10-25_pre_remediation/`
(gitignored, 40 files + frozen historical scores). Hashes are in
[evidence/2026-10-25_pre_remediation_manifest.json](evidence/2026-10-25_pre_remediation_manifest.json).
The remediated real-data release is `rel-20261025T191554` (policy `06c39b17c1e9d923`). Its
`results.json` is in `data/marts/`.

## Summary table

| Finding | Status | Files changed | Evidence / check | Remaining limitation |
|---|---|---|---|---|
| **A1** bootstrap froze response counts | **Fixed** | `evaluation/metrics.py`, `policy.py`, `evaluation/diagnostics.py`, `tests/test_metrics.py`, `tests/test_policy.py`, `tests/fixtures/qini_reference.json`, `scripts/make_qini_reference.py`, `scripts/coverage_diagnostic.py` | Customer-within-arm bootstrap with shared draws. Blanket interval on real data is now ₽72–₽933 (was 499.86–499.86). Identical-policy paired difference is exactly 0 in tests. Repeated-experiment coverage (200 sims × 2 scenarios): ATE 0.970 / 0.960, fixed-policy value 0.965 / 0.945, MC SE ≈ 0.013 ([evidence](evidence/bootstrap_coverage_diagnostic.json)). 4 sklift 0.5.1 reference cases (ties, negative effect, 20% treated) match to 1e-12. Input contract tests. | Intervals are fixed-score: they exclude fitting, calibration and selection uncertainty (labelled everywhere). Percentile method, no BCa. |
| **A2** deployed rule never evaluated as a policy | **Fixed** (within internal-validation limits) | `pipeline.py` (`fit_procedure`, `assess`), `policy.py`, `results.py`, dbt `mart_assessment_*` | 5 outer folds × (inner 5-fold selection + OOF calibrator + refit + strict threshold + response comparator optimised on training). Validation labels never enter selection or calibration (test `test_assessment_never_fits_on_validation_labels`). Pooled over 200,039 customers, the deployed policy texts 33.7%: **₽1,156 [901, 1,435]** per 1,000; vs text-everyone **+₽656 [+303, +1,029]**; vs optimised response (texts 89.8%) +₽700 [+349, +1,090]; expected random at the same share ₽168 [24, 314]. Calibration gap 0.63 pp (weighted, 10 bins). Transfer KS ≤ 0.031; eligible shares OOF vs validation within 1.5 pp. | Retrospective **internal** validation on customers seen during earlier exploration; not independent confirmation. The champion changes across folds (S, X, S, S, ensemble), which is real selection variability not captured by the fixed-policy intervals. The full-data refit selects 26.7% of all 400k customers vs 33.7% of labelled customers in validation. That difference reflects a different population and a refit; the release's specific full-data bundle is not itself separately validated. Calibration overpredicts bins 2–4 by ~0.7–0.9 pp (below break-even, so they are not texted). |
| **A3** "evaluated once" and lineage claims | **Fixed** (claims); **Partially** (lock) | `lineage.py`, `release.py`, `warehouse.py`, `cli.py`, `scripts/snapshot_evidence.py`, `scripts/freeze_historical_test_scores.py`, README, methodology, site, dashboard | Wording corrected everywhere: "reused holdout", 3 logged accesses (2 old pipeline runs + 1 logged freeze of per-customer scores for A1 recomputation) + audit reuse. `meta.runs` records git commit + dirty flag, source/config/dataset/feature/env fingerprints, seed, parents; all real-run rows have a commit and source fingerprint. History tables are append-by-run_id (test: rerun adds a run, the old one remains queryable). Historical scores are verified against the committed manifest hash before use. | The access log is an audit aid, not a security lock (any Python can read the data). The audit's own reuse is recorded in the audit, not in `meta.test_access_log`. Runs from a dirty tree are recorded as dirty, not refused. |
| **A4** future-data guard missed purchases | **Fixed** | `dbt/models/sources.yml`, `staging/stg_purchase_lines.sql`, `stg_transactions.sql`, `stg_clients.sql`, `stg_data_quality.sql`, all `features/*.sql`, `macros/as_of.sql`, `macros/test_no_future_dates.sql`, `macros/test_consistent_within.sql`, `configs/pipeline.yaml` | One configured `as_of` (no dbt default) passed by the CLI. All purchase features, product mix included, read `stg_purchase_lines` (`< as_of`). Windows are half-open elapsed-time intervals. Source tests fail the build on future or null timestamps, contradictory transaction fields, orphan client/product keys, train/test overlap and non-binary treatment/outcome. **Real build: 63/63 pass.** Raw-fixture variants `future_transaction`, `contradictory`, `unmatched_product` and `null_transaction_ts` each **fail the real dbt build** (integration tests). | The exact SMS timestamp is unpublished: `as_of` is a documented feature cutoff, not a send time. Timestamps carry no timezone. |
| **A5** serving authorizes unusable records; holdout not enforced | **Fixed** | `contract.py`, `api.py`, `pipeline.py` (`assign_campaign`), `cli.py` (`export-send-list`), `data.py`, tests | Typed contract: unknown keys, non-numeric, non-finite, out-of-domain, bad category, missing core features or <60% completeness → 422 (`{}`, gender "X", age −999, `"Infinity"` all tested). `/score` returns `economically_eligible` with an explicit "not authorization" note. `/campaigns/{id}/authorize` enforces the frozen assignment: real container check gave holdout→denied, send→authorized, ineligible→denied, unknown→denied. Assignment = sha256(campaign:client)/2⁶⁴: order- and population-independent (tests). `/health` vs `/ready` (503 without a release, verified in a container). **Batch/API parity on 200 real customers: max score difference 0.0.** | No live SMS provider (deliberately). The campaign is frozen per release; a new release creates a new frozen assignment under the same campaign id. Treat the campaign id as versioned in a real deployment. |
| **A6** static HTML vs Plotly renderer | **Fixed** | `scripts/build_site.py`, `scripts/browser_check.py`, CI | The page loads plotly.js **once** from the URL the installed Plotly emits (`plotly-4.1.1.min.js` for Plotly 7.1.0), replacing the hard-coded 2.35.2. Real Chrome 156 check (desktop + 390 px phone): 6/6 plots with finite numeric trace data, 6/6 hover tooltips, 0 console errors, 0 px overflow ([evidence](evidence/browser_check.json)). This check also caught and fixed NaN bars for empty groups. | The **published** GitHub Pages site still serves the old page until this branch is pushed. CI's browser job has not run on GitHub yet. |
| **A7** client search crashed on `[` | **Fixed** | `dashboard/views/clients.py` | `regex=False, na=False`. AppTest: `[`, `(`, `.` raise nothing and `.` matches no ids. Real Chrome: 0 exceptions for all three. | — |
| **A8** wrong age band; anomalies survive | **Fixed** | `dim_client.sql`, `stg_clients.sql`, `feat_demographics.sql`, `macros/test_age_band_consistent.sql`, dashboard/site | Band `14-17` added; a dbt test asserts every age is inside its band. Redemption before issue (536) → unknown + flag; post-cutoff redemption (44,953) → unknown; invalid age (1,779) → unknown; all counted in `stg_data_quality`. `days_issue_to_redeem` minimum is now ≥ 0. The fixture's 16-year-old lands in `14-17`. | No business eligibility rule (e.g. excluding minors) is applied: that is a campaign-owner decision, flagged in README limitations. |
| **A9** surrogate fidelity on training rows | **Fixed** | `pipeline.py` (`explain`), `drivers.py`, site | 70/30 split: **held-out R² 0.951**, RMSE 0.0065 (training R² 0.983 shown separately), plus error by score decile. Exact S-learner treatment-on − treatment-off TreeSHAP on the log-odds scale: max additivity error **6.6e-15**. All wording says "raw-score attribution". | The exact attribution is on the log-odds scale, not probability uplift, so the dashboard importance still comes from the surrogate. Neither explains calibrated effects or profit. |
| **A10** CI bypassed the data path | **Fixed** locally; CI **unverified on GitHub** | `tests/raw_fixture.py`, `tests/test_integration.py`, `scripts/build_fixture_release.py`, `.github/workflows/ci.yml`, `.dockerignore`, `Dockerfile`, `docker-compose.yml` | Deterministic raw fixture in the real gz schema. `ute run` through the real entry points (ingest → dbt → assess → deploy → historical → explain → marts → release), then API, dashboard and site smoke tests; expected dbt failures; an incomplete release never activates. `ingest.py` 0%→87%, `cli.py` 0%→95% coverage; total 93%. **Docker exercised for real** (Colima, Docker 29.5.2): dashboard container serves committed marts (2 plots, 0 exceptions in Chrome); API container is alive with /ready 503 without a release, and ready with the real release mounted. | The new CI workflow has not executed on GitHub runners (nothing pushed). Docker was verified on Colima/arm64, not on the CI's amd64 runner. |
| **A11** wheel and version contracts | **Fixed** (by documenting source-checkout mode) | `config.py`, `cli.py`, `pyproject.toml`, `uv.lock`, README | Supported mode: source checkout or `UTE_ROOT`. An installed wheel outside a checkout stops with a clean "cannot find the project root" (exit 2, tested). With `UTE_ROOT` the installed wheel loads the config. Floors raised to the APIs used and **verified**: `uv run --isolated --resolution lowest-direct` runs the full suite (numpy 2.0.0, pandas 2.2.2, duckdb 1.1.0, LightGBM 4.5.0, sklearn 1.5.0, SciPy 1.13.0, Streamlit **1.52.0**, Plotly 6.0.0, FastAPI 0.115.0, pydantic 2.7.0). The lowest runs found three real floor bugs: `st.metric(delta_arrow=)` needs Streamlit 1.52, PyYAML 6.0 cannot build on 3.12, and Typer < 0.15.4 either rejects `Path | None` options or breaks with current Click, so the `ute` CLI itself crashed at the old floor. A Typer `--help` test for every subcommand now guards that. Python narrowed to 3.12. CI pins uv and uses `--locked`; the Docker `--locked` build caught a stale lock. | A standalone wheel is not a runnable pipeline (configs and dbt are project files). Python 3.11/3.13 are untested and not advertised. |
| **A12** monitoring and segmentation wording | **Fixed** | `monitoring.py`, `health.py`, `segments.py`, `pipeline.py` | Balance now covers numeric SMD (non-missing), every category level and every feature's missingness (tests show a categorical/missingness imbalance a scalar mean hides). Wording: "consistent with documented random assignment … not proof". PSI is labelled a same-snapshot population diagnostic, not a gate (statuses low/moderate/high). Groups renamed "Predicted persuadable" etc. and described as model-based labels; experiment slices show n and ±1.96 SE with a no-causal-claim note. | PSI is still not temporal monitoring: there is no new-campaign data to monitor. |
| **A13** dashboard economics mixed estimands | **Fixed** | `overview.py`, `simulator.py`, `segments.py`, `clients.py`, `common.py`, `results.py`, site, README renderer | Every number carries a source badge (outer-fold validation / historical reused test / model projection / OOF curve / observed slice). The simulator recomputes value, best depth and the sensitivity table from the current cost, margin, base and **holdout** controls (AppTest: margin change → metrics change, "(current)" row = 3/300 = 1.00%). The optimum is labelled an estimate beside no-send. Projections count only `send` customers. The response comparator is optimised on its own training data. The expected random policy is separate from one random draw. Send-list export = action `send` only. README and site numbers are generated from `results.json`. | The simulator's curve is the all-labelled OOF ranking curve (the selection data). It is labelled optimistic and is not the validated policy value. |

## Lower-priority items from the audit

| Item | Disposition |
|---|---|
| Unused `tuning` switch; `reference_date` outside the YAML | Removed tuning (and optuna). `as_of` lives only in `configs/pipeline.yaml`; dbt has no default. |
| Config validation | Candidates known and non-empty with ≥1 uplift learner; ensemble size 0 or 2..n; folds ≥ 2; positive margin/cost; holdout in [0, 1). Tests cover each rejection. |
| Unequal treatment and class transformation | Class transformation reweights arms to 50/50 (test with 20% treated: mean prediction within 0.08 of the true mean effect). |
| `load_raw` trusted configured hashes | Hashes the actual bytes at load and refuses mismatches. `meta.ingest_log` stores expected and actual digests per `ingest_id`. |
| Non-atomic releases; stale caches | Validated activation (atomic pointer + mart swap, repair of an interrupted swap). Dashboard caches keyed on the `RELEASE.json` id. The API re-reads the pointer per request and caches per release. |
| Metric validation | Empty, unequal-length, non-binary, non-finite, single-arm, invalid depth and degenerate Qini inputs: explicit errors or counted NaN. Tested. |
| "2.1× prior project" comparison | Removed from the README (different splits, not a controlled comparison). |
| Dataset terms vs MIT license | README states the MIT license covers code only; dataset terms are the competition's. Granular marts derived from the public data are still committed (`dim_client`, 400k rows); no legal conclusion is drawn. |
| `scripts/audit_profit_uncertainty.py` | Kept verbatim as the auditor's evidence. It targets the pre-remediation layout (`artifacts/`) and no longer runs as-is. Its estimand is now produced by `ute historical` from frozen scores: historical 41% policy − text everyone **₽514 [−223, +1,271]**, close to the audit's [−240, +1,253] (different seed and replicate count). |

## Revised metrics, by evidence type

1. **Outer-fold retrospective validation** (internal; whole procedure; 500 customer-within-arm
   draws; fixed fitted policies). The deployed calibrated policy texts 33.7%: ₽1,156 [901, 1,435]
   per 1,000. Paired vs text-everyone **+₽656 [+303, +1,029]** (share of draws > 0: 1.000). Paired
   vs the optimised response policy +₽700 [+349, +1,090]. Pooled raw-score Qini 0.0146
   [0.0119, 0.0174]. Fold values range ₽812–₽1,321.
2. **Historical reused test** (2026-10-24 bundle; corrected intervals). Qini +0.0164
   [+0.0102, +0.0224]; top-10% uplift +11.9 pp [+8.7, +14.9]. The 41% policy vs text-everyone is
   **₽514 [−223, +1,271]**: the interval includes zero, so "about 2×" was never established.
3. **Deployment projection** (model-based, not observed). 96,182 to text and 10,831 held out of
   107,013 eligible; projected 6,229 additional buyers and ₽367,477 among those texted, vs
   ₽200,025 for texting everyone.

None of these is independent confirmation. A future campaign evaluated against its holdout group
would be.

## Checks actually run

| Check | Result |
|---|---|
| `ruff format --check` + `ruff check` | pass |
| `pytest` (locked env: numpy 2.5.4, pandas 3.1.0, duckdb 2.0.0, LightGBM 4.7.0, sklearn 1.9.1, Streamlit 1.66.1, Plotly 7.1.0, dbt 1.12.6) | **99 passed**, 93% branch-aware coverage |
| `pytest` on lowest-direct dependencies (versions in A11; Typer 0.15.4, Click 8.1.8) | **99 passed** |
| Real dbt build (45.8M purchase lines) | 63/63 models + tests pass in 39 s |
| Real `ute run --skip-ingest` | all stages succeeded; release activated (assess 1,530 s, deploy 653 s) |
| Coverage simulation | in table (A1) |
| Real Chrome (Playwright, Chrome 156.0.8078.48): static site desktop + phone; all 7 Streamlit pages; literal search | pass ([evidence](evidence/browser_check.json)) |
| Docker on Colima (Docker 29.5.2): image build (`--locked`), dashboard container, API without and with release, campaign authorization in the container | pass |
| Installed wheel outside the checkout | clean failure without `UTE_ROOT`; loads with it |
| Batch/API parity, 200 real customers | max difference 0.0 |
| Fresh `git clone` of the final commit → `uv sync --locked --all-extras` → `pytest` | 99 passed; with no data, pipeline stages stop with a clear "run `ute transform`" message |

## Reproduce

```bash
uv sync --locked --all-extras --group browser
uv run ute run                     # or: --skip-ingest when data/raw is present
uv run python scripts/build_site.py && uv run python scripts/render_readme_results.py
uv run pytest
uv run --isolated --resolution lowest-direct --all-extras --group dev pytest
uv run --group browser python scripts/browser_check.py --site docs/index.html
docker compose up --build          # dashboard :8501, API :8000 (mount ./releases)
```

`ute historical` needs the local snapshot (`snapshots/2026-10-25_pre_remediation/`, verified
against its committed manifest). On a fresh clone without it, a full release cannot be activated.
That is deliberate: the historical numbers cannot be recomputed without the frozen scores.
