# Detailed SMS targeting results

_Generated from `data/marts/results.json` (release `rel-20261026T001416546751`, policy `f332857ade6fd420`). Economics are assumptions: SMS ₽3.00, ₽105 per additional buyer (break-even uplift 2.85%)._

**1 · Outer-fold retrospective validation of the deployed policy** (all 200,039 labelled experiment customers, each validated once by a procedure fitted without its fold; retrospective internal validation on customers already used during earlier exploration; not independent confirmation).

| Policy | Customers texted | Value per 1,000 customers [95% CI] |
|---|---|---|
| Deployed policy (calibrated threshold) | 42.0% | ₽1,184 [₽887, ₽1,477] |
| Response model, own best share | 91.2% | ₽494 [₽85, ₽927] |
| Random, same share (expected) | 42.0% | ₽210 [₽30, ₽392] |
| Text everyone | 100.0% | ₽500 [₽72, ₽933] |
| Text nobody | 0.0% | ₽0 [₽0, ₽0] |

| Paired difference (same draws) | Per 1,000 [95% CI] | Share of draws > 0 |
|---|---|---|
| Deployed − text everyone | ₽684 [₽361, ₽1,014] | 1.000 |
| Deployed − text nobody | ₽1,184 [₽887, ₽1,477] | 1.000 |
| Deployed − response model, own best share | ₽690 [₽349, ₽1,000] | 1.000 |

In short: texting 42% of customers (58% fewer sends than texting everyone) had 2.4× the estimated text-everyone value. The ratio is a point estimate; the interval above is for the difference.

Intervals: 500 customer-within-arm bootstrap draws for the fixed fitted policies; they exclude model-fitting and selection uncertainty. Deployed value by outer fold ranged ₽818 to ₽1,505 per 1,000 (folds share training data). Champion by fold: s_learner, ensemble:class_transformation+s_learner+x_learner, s_learner, s_learner, ensemble:dr_learner+s_learner+x_learner.

Calibration on validation folds: weighted mean |predicted − observed| uplift 0.56 pp across 10 bins. OOF-to-refit transfer: KS ≤ 0.028; eligible share (inner OOF vs validation) within 1.2 pp in every fold.

**2 · Historical reused test** (the 2026-10-24 bundle; not independent confirmation).

| Model | Qini AUC [95% CI] | Uplift in top 10%, pp [95% CI] |
|---|---|---|
| champion | +0.0164 [+0.0102, +0.0224] | +11.9 [+8.7, +14.9] |
| response | -0.0054 [-0.0108, +0.0004] | +0.9 [-0.2, +2.0] |
| random_draw | -0.0015 [-0.0073, +0.0058] | +2.9 [+0.3, +5.9] |

Historical 41% policy − text everyone: ₽514 [−₽223, ₽1,271] per 1,000 (share of draws > 0: 0.914).

**3 · Deployment projection** (model-based scenario, not an observed result): 400,162 customers scored, 102,813 eligible, 92,432 to text and 10,381 held out (10% random holdout); projected 6,211 additional buyers and ₽376,892 among those texted, vs ₽200,025 for texting everyone under the same assumptions.
