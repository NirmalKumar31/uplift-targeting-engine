# Claude handoff receipt

Receipt confirmed. I have the full brief, the audit findings A1–A13, the evidence table, and the implementation order. I will read `docs/AUDIT_2026-10-25.md` and `docs/CLAUDE_IMPLEMENTATION_BRIEF.md` in full before touching any code.

The first three priorities, in order:

**Priority 0 — Preserve evidence and correct claims**
Before any rebuild: snapshot existing model artifacts, result tables, scores, and run logs with a manifest and hashes (no binaries in git). Then immediately correct misleading wording in README, `docs/methodology.md`, and the generated static site — specifically: the test was evaluated twice (not once), Qini +0.0164 and the ~2× profit are historical point estimates, the deployed calibrated send rule has never been independently evaluated, and R² 0.98 was training surrogate fit. The audit's bootstrap interval (−₽240 to +₽1,253 per 1,000) is preserved as a diagnostic, not a new confirmatory result.

**Priority 1 — Repair bootstrap inference (A1)**
Replace the outcome-stratified resample in `evaluation/metrics.py:105` with customer-level resampling within each treatment arm, keeping `(X, score, outcome)` paired across model and policy comparisons. The current approach fixes all four treatment×outcome cell sizes, producing zero-width intervals (blanket profit_low = profit_high in the mart). Add validation, relabel the bootstrap win fraction as a fraction (not a posterior probability), and add coverage simulations confirming nonzero-width ATE intervals on nondegenerate data.

**Priority 2 — Evaluate the complete deployed policy (A2, A13)**
The calibrated cost-threshold send rule has never been evaluated on held-out data; `evaluate` tested raw-score ranking at 41% depth while `deploy` fits on all labeled customers including the former test set. Implement an outer-validation loop where both the learner and calibrator are fit without touching outer-fold labels, then evaluate the exact deployed action rule on that fold with paired comparisons against blanket/no-send/response baselines.

No implementation has begun. Ready to proceed when you confirm.
