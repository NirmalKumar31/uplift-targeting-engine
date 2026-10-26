"""Build the static dashboard page for GitHub Pages from the active release's marts.

uv run python scripts/build_site.py [--marts data/marts] [--out docs/index.html]
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

ROOT = Path(__file__).resolve().parents[1]
# Chart ids checked by scripts/browser_check.py.
CHART_IDS = {
    "Policy value, outer-fold validation": "policy-value",
    "Calibration on held-out folds": "calibration",
    "Qini curve, historical reused test": "hist-qini",
    "Observed effect by out-of-fold score decile": "decile-effect",
    "Predicted group mix by age band": "group-mix",
    "Drivers of the raw uplift score": "drivers",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--marts", type=Path, default=ROOT / "data" / "marts")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "index.html")
    args = ap.parse_args()
    os.environ["UTE_MARTS_DIR"] = str(args.marts)
    sys.path.insert(0, str(ROOT / "dashboard"))
    from common import AGE_BANDS, BLUE, GROUP_COLORS, GROUPS, MUTED, ORANGE, model_label, style

    res = json.loads((args.marts / "results.json").read_text())

    def mart(name: str) -> pd.DataFrame:
        return pd.read_parquet(args.marts / f"{name}.parquet")

    econ, A, H, P = (
        res["economics"],
        res["assessment"],
        res["historical_test"],
        res["deployment_projection"],
    )
    cur = "₽" if econ["currency"] == "RUB" else econ["currency"] + " "

    def money(v):
        if v is None or pd.isna(v):
            return "n/a"
        return f"{cur}{v:,.0f}" if v >= 0 else f"−{cur}{abs(v):,.0f}"

    figures: list[tuple[str, str, go.Figure, str]] = []

    pol = pd.DataFrame(A["policies"]).set_index("policy")
    order = [
        o
        for o in ["deployed", "response_optimized", "random_expected", "blanket", "no_send"]
        if o in pol.index
    ]
    p = pol.loc[order]
    fig = go.Figure(
        go.Bar(
            y=[model_label(i) for i in p.index],
            x=p.value_per_1000,
            orientation="h",
            marker_color=[
                BLUE if i == "deployed" else (ORANGE if "response" in i else MUTED) for i in p.index
            ],
            error_x=dict(
                type="data",
                symmetric=False,
                color=MUTED,
                thickness=1,
                array=(p.value_high - p.value_per_1000).fillna(0),
                arrayminus=(p.value_per_1000 - p.value_low).fillna(0),
            ),
            hovertemplate="%{y}: %{x:,.0f} per 1,000<extra></extra>",
        )
    )
    fig.add_vline(x=0, line=dict(color=MUTED, width=1))
    fig.update_layout(
        xaxis_title="Value vs texting nobody, per 1,000",
        yaxis=dict(autorange="reversed"),
        bargap=0.35,
    )
    figures.append(
        (
            "assessment",
            "Policy value, outer-fold validation",
            style(fig, 360, legend=False),
            "95% intervals: customer resampling within arm for fixed fitted policies.",
        )
    )

    cal = mart("mart_assessment_calibration").query("fold == 'pooled'").sort_values("bin")
    fig = go.Figure()
    fig.add_bar(
        x=cal.bin,
        y=cal.observed_uplift * 100,
        name="Observed",
        marker_color=BLUE,
        error_y=dict(type="data", array=1.96 * cal.observed_se * 100, color=MUTED, thickness=1),
    )
    fig.add_scatter(
        x=cal.bin,
        y=cal.predicted_effect * 100,
        name="Calibrated prediction",
        mode="lines+markers",
        line=dict(color=ORANGE, width=2),
        marker=dict(size=8),
    )
    fig.update_layout(
        xaxis=dict(title="Calibrated-effect bin", dtick=1), yaxis_title="Uplift (pp)", bargap=0.25
    )
    figures.append(
        (
            "assessment",
            "Calibration on held-out folds",
            style(fig, 360),
            "Each prediction comes from a policy fitted without the customer's fold.",
        )
    )

    hist_ok = H.get("available", True)
    curves = mart("mart_qini_curves").query("source == 'historical reused test'")
    fig = go.Figure()
    ref = curves[curves.model == "champion"]
    fig.add_scatter(
        x=ref.fraction * 100,
        y=ref.random_line,
        mode="lines",
        name="Random line",
        meta="decorative",
        line=dict(color=MUTED, width=1.5),
        hoverinfo="skip",
    )
    for name, color in (("champion", BLUE), ("response", ORANGE)):
        d = curves[curves.model == name]
        fig.add_scatter(
            x=d.fraction * 100,
            y=d.incremental_buyers,
            mode="lines",
            name=model_label(name),
            line=dict(color=color, width=2),
        )
    fig.update_layout(xaxis_title="Share texted (%)", yaxis_title="Incremental buyers")
    if hist_ok:
        figures.append(
            (
                "historical",
                "Qini curve, historical reused test",
                style(fig, 360),
                "Old bundle on its reused 40,008-customer test split.",
            )
        )

    cate = mart("mart_experiment_cate").query("dimension == 'Predicted decile (OOF)'")
    cate = cate.sort_values("level")
    fig = go.Figure(
        go.Bar(
            x=cate.level.str.replace("Decile ", ""),
            y=cate.observed_uplift * 100,
            marker_color=BLUE,
            error_y=dict(type="data", array=1.96 * cate.std_error * 100, color=MUTED, thickness=1),
        )
    )
    fig.update_layout(
        xaxis_title="Out-of-fold score decile (1 = highest)", yaxis_title="Observed uplift (pp)"
    )
    figures.append(
        (
            "experiment",
            "Observed effect by out-of-fold score decile",
            style(fig, 340, legend=False),
            "Raw experiment differences, no model.",
        )
    )

    seg = mart("mart_segment_summary")
    mix = seg.groupby(["age_band", "predicted_group"], as_index=False).n_clients.sum()
    mix["share"] = mix.n_clients / mix.groupby("age_band").n_clients.transform("sum")
    bands = [b for b in AGE_BANDS if b in set(mix.age_band)]  # omit bands with no customers
    fig = go.Figure()
    for g in GROUPS:
        d = mix[mix.predicted_group == g].set_index("age_band").reindex(bands)
        fig.add_bar(
            y=bands,
            x=d.share.fillna(0),
            name=g,
            orientation="h",
            marker=dict(color=GROUP_COLORS[g], line=dict(color="#fcfcfb", width=2)),
        )
    fig.update_layout(
        barmode="stack", xaxis=dict(tickformat=".0%"), yaxis=dict(autorange="reversed")
    )
    figures.append(
        (
            "projection",
            "Predicted group mix by age band",
            style(fig, 380),
            "Predicted business labels, not observed individual response types.",
        )
    )

    imp = mart("mart_feature_importance").sort_values("share").tail(12)
    fid = pd.DataFrame(res["explainability"]["surrogate_fidelity"]).set_index("split")
    fig = go.Figure(go.Bar(y=imp.feature, x=imp.share * 100, orientation="h", marker_color=BLUE))
    fig.update_layout(xaxis_title="% of attribution", bargap=0.25)
    figures.append(
        (
            "projection",
            "Drivers of the raw uplift score",
            style(fig, 420, legend=False),
            f"Surrogate TreeSHAP; held-out fidelity R² "
            f"{fid.loc['held-out rows', 'r2']:.2f}. Explains the model, not causes.",
        )
    )

    dif = pd.DataFrame(A["differences"]).query("policy == 'deployed'")
    hd = pd.DataFrame(H.get("differences", []))
    hm = pd.DataFrame(H.get("models", [{"model": "champion"}])).set_index("model")
    dep = pol.loc["deployed"]

    def tile(k, v, sub=""):
        return (
            f"<div class='tile'><div class='k'>{html.escape(k)}</div>"
            f"<div class='v'>{html.escape(v)}</div>"
            + (f"<div class='s'>{html.escape(sub)}</div>" if sub else "")
            + "</div>"
        )

    sections = {
        "assessment": (
            "1 · Outer-fold retrospective validation of the deployed policy",
            A["status"] + ". " + A["population"] + ".",
            [
                tile(
                    "Deployed policy value / 1,000",
                    money(dep.value_per_1000),
                    f"[{money(dep.value_low)}, {money(dep.value_high)}]",
                ),
                tile("Share texted", f"{dep.selected_share:.1%}"),
            ]
            + [
                tile(
                    f"vs {model_label(r.reference).lower()}",
                    money(r.difference_per_1000),
                    f"[{money(r.difference_low)}, {money(r.difference_high)}]",
                )
                for r in dif.itertuples()
            ],
        ),
        "historical": (
            "2 · Historical reused test (2026-10-24 bundle)",
            f"Unavailable for this release: {H.get('reason')}. No historical numbers are shown."
            if not hist_ok
            else H["source"] + ".",
            []
            if not hist_ok
            else [
                tile(
                    "Champion Qini",
                    f"{hm.loc['champion', 'qini_auc']:+.4f}",
                    f"[{hm.loc['champion', 'qini_auc_low']:+.4f}, "
                    f"{hm.loc['champion', 'qini_auc_high']:+.4f}]",
                )
            ]
            + [
                tile(
                    f"41% policy − {model_label(r.reference).lower()}",
                    money(r.difference_per_1000),
                    f"[{money(r.difference_low)}, {money(r.difference_high)}]",
                )
                for r in hd.query("policy == 'champion_at_41pct'").itertuples()
            ],
        ),
        "experiment": ("Observed experiment", "Raw treated − control differences.", []),
        "projection": (
            "3 · Deployment projection (model-based scenario)",
            P["status"] + ".",
            [
                tile("Customers scored", f"{P['n_scored']:,}"),
                tile("Texted / held out", f"{P['n_send']:,} / {P['n_holdout']:,}"),
                tile(
                    "Projected additional buyers", f"{P['projected_additional_buyers_sent']:,.0f}"
                ),
                tile(
                    "Projected value",
                    money(P["projected_value_sent"]),
                    f"text everyone: {money(P['projected_value_blanket'])}",
                ),
            ],
        ),
    }

    first = True
    blocks = []
    for key in ("assessment", "historical", "experiment", "projection"):
        title, lede, tiles = sections[key]
        cards = []
        for k2, h, fig, note in figures:
            if k2 != key:
                continue
            body = fig.to_html(
                div_id=CHART_IDS[h],
                full_html=False,
                include_plotlyjs="cdn" if first else False,
                default_width="100%",
                config={"displayModeBar": False, "responsive": True},
            )
            first = False
            cards.append(
                f"<section class='card'><h3>{html.escape(h)}</h3>{body}"
                f"<p class='note'>{html.escape(note)}</p></section>"
            )
        blocks.append(
            f"<h2>{html.escape(title)}</h2><p class='lede'>{html.escape(lede)}</p>"
            + (f"<div class='tiles'>{''.join(tiles)}</div>" if tiles else "")
            + f"<div class='grid'>{''.join(cards)}</div>"
        )

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Uplift Targeting Engine</title>
<style>
  :root {{ --bg:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
          --line:rgba(11,11,11,.10); }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }}
  main {{ max-width:1180px; margin:0 auto; padding:32px 16px 64px; }}
  h1 {{ font-size:28px; margin:0 0 8px; }} h2 {{ font-size:20px; margin:36px 0 4px; }}
  h3 {{ font-size:15px; margin:0 0 6px; }}
  .lede {{ color:var(--ink2); max-width:860px; line-height:1.5; }}
  .status {{ background:#eef4fc; border:1px solid #cde2fb; border-radius:10px; padding:12px 14px; }}
  .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:12px;
           margin:16px 0; }}
  .tile {{ background:var(--surface); border:1px solid var(--line); border-radius:10px;
          padding:12px 14px; }}
  .tile .k {{ color:var(--muted); font-size:13px; }} .tile .v {{ font-size:22px; font-weight:600; }}
  .tile .s {{ color:var(--ink2); font-size:12px; margin-top:2px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,480px),1fr));
          gap:16px; }}
  .card {{ background:var(--surface); border:1px solid var(--line); border-radius:12px;
          padding:16px; min-width:0; overflow:hidden; }}
  .note {{ color:var(--ink2); font-size:13px; margin:6px 0 0; }}
  footer {{ color:var(--muted); font-size:13px; margin-top:32px; }} a {{ color:#2a78d6; }}
</style></head><body><main>
<h1>Who should get the promo SMS?</h1>
<p class="lede">Causal uplift modelling on the X5 RetailHero randomised SMS experiment. The deployed
rule texts a customer when the calibrated effect × an assumed {money(econ["margin_per_additional_buyer"])}
per additional buyer exceeds the {cur}{econ["sms_cost"]:.2f} SMS cost. Release
<code>{html.escape(res["release_id"])}</code>, policy <code>{html.escape(res["policy_version"])}</code>.</p>
<p class="lede status"><b>Evidence status.</b> Three kinds of numbers below are not interchangeable:
internal outer-fold validation of the whole policy (on customers used in earlier exploration),
a historical test split that has been reused, and model-based projections. None is independent
confirmation. Economics are assumptions; intervals cover customer sampling for fixed fitted models.</p>
{"".join(blocks)}
<footer>Static snapshot generated by <code>scripts/build_site.py</code> from the active release's
<code>results.json</code> and marts. Interactive version: the Streamlit app in the repository.
Source: <a href="https://github.com/NirmalKumar31/uplift-targeting-engine">github.com/NirmalKumar31/uplift-targeting-engine</a></footer>
</main>
<script>window.addEventListener('load', () =>
  document.querySelectorAll('.js-plotly-plot').forEach((g) => Plotly.Plots.resize(g)));</script>
</body></html>"""
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page)
    print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
