"""Rewrite the README results block from data/marts/results.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START, END = "<!-- results:start -->", "<!-- results:end -->"

LABELS = {
    "deployed": "Deployed policy (calibrated threshold)",
    "response_optimized": "Response model, own best share",
    "random_expected": "Random, same share (expected)",
    "blanket": "Text everyone",
    "no_send": "Text nobody",
    "champion_at_41pct": "Historical champion at 41%",
    "response_at_41pct": "Response model at 41%",
    "random_expected_41pct": "Random 41% (expected)",
}


def rub(v: float) -> str:
    return f"₽{v:,.0f}" if v >= 0 else f"−₽{abs(v):,.0f}"


def ci(lo: float, hi: float) -> str:
    return f"[{rub(lo)}, {rub(hi)}]"


def render(res: dict) -> str:
    A, H, P, E = (
        res["assessment"],
        res["historical_test"],
        res["deployment_projection"],
        res["economics"],
    )
    pol = {p["policy"]: p for p in A["policies"]}
    lines = [
        f"_Generated from `data/marts/results.json` (release `{res['release_id']}`, policy "
        f"`{res['policy_version']}`). Economics are assumptions: SMS ₽{E['sms_cost']:.2f}, "
        f"₽{E['margin_per_additional_buyer']:.0f} per additional buyer (break-even uplift "
        f"{E['break_even_uplift']:.2%})._",
        "",
        "**1 · Outer-fold retrospective validation of the deployed policy** "
        f"({A['population']}; {A['status']}).",
        "",
        "| Policy | Customers texted | Value per 1,000 customers [95% CI] |",
        "|---|---|---|",
    ]
    for key in ("deployed", "response_optimized", "random_expected", "blanket", "no_send"):
        p = pol[key]
        lines.append(
            f"| {LABELS[key]} | {p['selected_share']:.1%} | "
            f"{rub(p['value_per_1000'])} {ci(p['value_low'], p['value_high'])} |"
        )
    lines += [
        "",
        "| Paired difference (same draws) | Per 1,000 [95% CI] | Share of draws > 0 |",
        "|---|---|---|",
    ]
    for d in A["differences"]:
        if d["policy"] == "deployed":
            lines.append(
                f"| Deployed − {LABELS[d['reference']].lower()} | "
                f"{rub(d['difference_per_1000'])} "
                f"{ci(d['difference_low'], d['difference_high'])} | "
                f"{d['frac_positive']:.3f} |"
            )
    dep, blk = pol["deployed"], pol["blanket"]
    if blk["value_per_1000"] > 0:
        lines += [
            "",
            f"In short: texting {dep['selected_share']:.0%} of customers "
            f"({1 - dep['selected_share']:.0%} fewer sends than texting everyone) earned "
            f"{dep['value_per_1000'] / blk['value_per_1000']:.1f}× the text-everyone value. "
            "The ratio is a point estimate; the interval above is for the difference.",
        ]
    lo, hi = A["fold_value_range_deployed"]
    max_ks = max(t["ks_statistic_oof_vs_refit"] for t in A["transfer"])
    max_gap = 100 * max(
        abs(t["eligible_share_inner_oof"] - t["eligible_share_validation"]) for t in A["transfer"]
    )
    lines += [
        "",
        f"Intervals: {A['bootstrap']['n_boot']} customer-within-arm bootstrap draws for the fixed "
        f"fitted policies; they exclude model-fitting and selection uncertainty. Deployed value "
        f"by outer fold ranged {rub(lo)} to {rub(hi)} per 1,000 (folds share training data). "
        f"Champion by fold: {', '.join(A['champions_by_fold'])}.",
        "",
        f"Calibration on validation folds: weighted mean |predicted − observed| uplift "
        f"{100 * A['calibration_pooled']['weighted_mean_abs_gap']:.2f} pp across 10 bins. "
        f"OOF-to-refit transfer: KS ≤ {max_ks:.3f}; eligible share (inner OOF vs validation) "
        f"within {max_gap:.1f} pp in every fold.",
        "",
        "**2 · Historical reused test** (the 2026-10-24 bundle; not independent confirmation).",
        "",
    ]
    if not H.get("available", True):
        lines.append(f"_Unavailable for this release: {H['reason']}. No numbers substituted._")
    else:
        lines += ["| Model | Qini AUC [95% CI] | Uplift in top 10%, pp [95% CI] |", "|---|---|---|"]
    for m in H.get("models", []):
        lines.append(
            f"| {m['model']} | {m['qini_auc']:+.4f} "
            f"[{m['qini_auc_low']:+.4f}, {m['qini_auc_high']:+.4f}] | "
            f"{100 * m['uplift_at_10']:+.1f} [{100 * m['uplift_at_10_low']:+.1f}, "
            f"{100 * m['uplift_at_10_high']:+.1f}] |"
        )
    for d in H.get("differences", []):
        if d["policy"] == "champion_at_41pct" and d["reference"] == "blanket":
            lines += [
                "",
                f"Historical 41% policy − text everyone: "
                f"{rub(d['difference_per_1000'])} "
                f"{ci(d['difference_low'], d['difference_high'])} per 1,000 "
                f"(share of draws > 0: {d['frac_positive']:.3f}).",
            ]
    lines += [
        "",
        "**3 · Deployment projection** (model-based scenario, not an observed result): "
        f"{P['n_scored']:,} customers scored, {P['n_eligible']:,} eligible, {P['n_send']:,} to "
        f"text and {P['n_holdout']:,} held out ({P['holdout_share']:.0%} random holdout); "
        f"projected {P['projected_additional_buyers_sent']:,.0f} additional buyers and "
        f"{rub(P['projected_value_sent'])} among those texted, vs "
        f"{rub(P['projected_value_blanket'])} for texting everyone under the same assumptions.",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--marts", type=Path, default=ROOT / "data" / "marts")
    ap.add_argument("--readme", type=Path, default=ROOT / "README.md")
    args = ap.parse_args()
    res = json.loads((args.marts / "results.json").read_text())
    text = args.readme.read_text()
    if START not in text or END not in text:
        raise SystemExit("README has no results markers")
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    args.readme.write_text(f"{head}{START}\n{render(res)}\n{END}{tail}")
    print(f"README results block regenerated from release {res['release_id']}")


if __name__ == "__main__":
    main()
