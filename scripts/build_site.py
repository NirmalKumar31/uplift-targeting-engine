"""Build a static, shareable snapshot of the BI dashboard for GitHub Pages (docs/index.html).

    uv run python scripts/build_site.py

Reads the exported dbt marts in data/marts; the interactive version is the Streamlit app.
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from common import (  # noqa: E402
    BLUE,
    GRID,
    INK2,
    MUTED,
    ORANGE,
    SEGMENT_COLORS,
    SEGMENT_ORDER,
    model_label,
    style,
)

MARTS = ROOT / "data" / "marts"


def load(name: str) -> pd.DataFrame:
    return pd.read_parquet(MARTS / f"{name}.parquet")


def fig_html(fig: go.Figure) -> str:
    """Chart title becomes an HTML subtitle; standalone plotly needs explicit automargins."""
    title = fig.layout.title.text or ""
    fig.update_layout(title=None, margin=dict(l=8, r=8, t=40 if fig.layout.showlegend else 12, b=8))
    fig.update_xaxes(automargin=True)
    fig.update_yaxes(automargin=True)
    sub = f"<p class='sub'>{html.escape(title)}</p>" if title else ""
    return sub + fig.to_html(
        full_html=False,
        include_plotlyjs=False,
        default_width="100%",
        config={"displayModeBar": False, "responsive": True},
    )


def main() -> None:
    policy = load("mart_campaign_policy").iloc[0]
    test = load("mart_test_scorecard")
    champ = test[test.role == "champion"].iloc[0]
    resp = test[test.model == "response"].iloc[0]
    board = load("mart_model_leaderboard").sort_values("qini_auc")
    figs = []

    # 1. Qini on test
    curves = load("mart_qini_curves").query("split == 'test'")
    fig = go.Figure()
    total = curves[curves.model == champ.model].incremental_buyers.iloc[-1]
    fig.add_scatter(
        x=[0, 100],
        y=[0, total],
        mode="lines",
        name="Random targeting",
        line=dict(color=MUTED, width=1.5),
        hoverinfo="skip",
    )
    for name, color in ((champ.model, BLUE), ("response", ORANGE)):
        d = curves[curves.model == name]
        fig.add_scatter(
            x=d.fraction * 100,
            y=d.incremental_buyers,
            mode="lines",
            name=model_label(name),
            line=dict(color=color, width=2),
            hovertemplate="Top %{x:.0f}%: %{y:,.0f} extra buyers<extra></extra>",
        )
    fig.update_layout(
        title="Qini curve, historical (reused) test split",
        xaxis_title="Share texted (%)",
        yaxis_title="Incremental buyers",
    )
    figs.append(
        (
            "Uplift model vs response model",
            style(fig),
            "Above the gray line beats random targeting. The response model targets "
            "likely buyers and does no better than random.",
        )
    )

    # 2. Extra purchases per 1,000
    ks = [10, 20, 30, 50]
    fig = go.Figure()
    for name, color, row in ((champ.model, BLUE, champ), ("response", ORANGE, resp)):
        fig.add_bar(
            x=[f"Top {k}%" for k in ks],
            y=[1000 * row[f"uplift_at_{k}"] for k in ks],
            name=model_label(name),
            marker_color=color,
            error_y=dict(
                type="data",
                symmetric=False,
                color=MUTED,
                thickness=1,
                array=[1000 * (row[f"uplift_at_{k}_high"] - row[f"uplift_at_{k}"]) for k in ks],
                arrayminus=[1000 * (row[f"uplift_at_{k}"] - row[f"uplift_at_{k}_low"]) for k in ks],
            ),
        )
    fig.add_hline(
        y=1000 * champ.ate,
        line=dict(color=MUTED, width=1.5),
        annotation_text=f"Random {1000 * champ.ate:.0f}",
        annotation_font_color=MUTED,
        annotation_position="top left",
    )
    fig.update_layout(
        title="Extra purchases per 1,000 texted (test, 95% CI)",
        barmode="group",
        bargap=0.3,
        yaxis_title="Incremental purchases / 1,000",
    )
    figs.append(("Where the extra purchases come from", style(fig), ""))

    # 3. Profit curve
    pc = load("mart_profit_curve").sort_values("depth")
    n = policy.n_scored / 1000
    fig = go.Figure()
    fig.add_scatter(
        x=list(pc.depth * 100) + list(pc.depth[::-1] * 100),
        y=list(pc.profit_high * n) + list(pc.profit_low[::-1] * n),
        fill="toself",
        fillcolor="rgba(42,120,214,0.12)",
        line=dict(width=0),
        name="95% interval",
        hoverinfo="skip",
    )
    fig.add_scatter(
        x=pc.depth * 100,
        y=pc.profit_per_1000 * n,
        name="Expected profit",
        line=dict(color=BLUE, width=2),
        hovertemplate="Top %{x:.0f}%: ₽%{y:,.0f}<extra></extra>",
    )
    fig.add_hline(y=0, line=dict(color=MUTED, width=1))
    fig.update_layout(
        title=f"Campaign profit across {policy.n_scored:,.0f} clients (₽)",
        xaxis_title="Share texted, highest uplift first (%)",
        yaxis_title="Profit",
    )
    figs.append(
        (
            "How deep to text",
            style(fig),
            f"Break-even uplift {policy.break_even_uplift:.2%} (SMS ₽{policy.sms_cost:.0f} ÷ "
            f"₽{policy.margin_per_purchase:.0f} margin per extra purchase).",
        )
    )

    # 4. Leaderboard
    fig = go.Figure()
    fig.add_vrect(
        x0=board.noise_floor_low.iloc[0],
        x1=board.noise_floor_high.iloc[0],
        fillcolor=GRID,
        opacity=0.6,
        line_width=0,
    )
    fig.add_scatter(
        x=board.qini_auc,
        y=board.model.map(model_label),
        mode="markers",
        marker=dict(
            size=11,
            color=[
                BLUE if c else (MUTED if b else INK2)
                for c, b in zip(board.is_champion, board.is_baseline, strict=True)
            ],
            line=dict(color="#fcfcfb", width=2),
        ),
        error_x=dict(
            type="data",
            symmetric=False,
            array=board.qini_auc_high - board.qini_auc,
            arrayminus=board.qini_auc - board.qini_auc_low,
            color=MUTED,
        ),
        hovertemplate="%{y}: %{x:+.4f}<extra></extra>",
    )
    fig.update_layout(
        title="Out-of-fold Qini AUC, 95% CI (gray band = random noise)",
        xaxis_title="Normalised Qini AUC",
    )
    figs.append(
        (
            "Every learner, one selection rule",
            style(fig, 420, legend=False),
            "Champion (blue) is chosen on out-of-fold Qini before the test set is scored.",
        )
    )

    # 5. Observed effect by predicted decile
    cate = (
        load("mart_experiment_cate").query("dimension == 'Predicted decile'").sort_values("level")
    )
    fig = go.Figure()
    fig.add_bar(
        x=cate.level.str.replace("Decile ", ""),
        y=cate.observed_uplift * 100,
        marker_color=BLUE,
        error_y=dict(type="data", array=1.96 * cate.std_error * 100, color=MUTED, thickness=1),
        hovertemplate="Decile %{x}: %{y:+.2f} pp<extra></extra>",
    )
    fig.update_layout(
        title="Observed SMS effect by predicted uplift decile (experiment)",
        xaxis_title="Decile (1 = most persuadable)",
        yaxis_title="Uplift (pp)",
    )
    figs.append(("The ranking holds in the raw experiment", style(fig, legend=False), ""))

    # 6. Segment mix
    seg = load("mart_segment_summary")
    mix = seg.groupby(["age_band", "segment"], as_index=False).n_clients.sum()
    mix["share"] = mix.n_clients / mix.groupby("age_band").n_clients.transform("sum")
    bands = ["18-24", "25-34", "35-44", "45-54", "55-64", "65+", "Unknown"]
    fig = go.Figure()
    for s in SEGMENT_ORDER:
        d = mix[mix.segment == s].set_index("age_band").reindex(bands)
        fig.add_bar(
            y=bands,
            x=d.share,
            name=s,
            orientation="h",
            marker=dict(color=SEGMENT_COLORS[s], line=dict(color="#fcfcfb", width=2)),
            hovertemplate="%{y}: %{x:.0%}<extra>" + s + "</extra>",
        )
    fig.update_layout(
        title="Customer segment mix by age band",
        barmode="stack",
        xaxis=dict(tickformat=".0%"),
        yaxis=dict(autorange="reversed"),
    )
    figs.append(("Who the persuadables are", style(fig, 400), ""))

    # 7. Drivers
    imp = load("mart_feature_importance").sort_values("share").tail(12)
    fig = go.Figure()
    fig.add_bar(
        y=imp.feature,
        x=imp.share * 100,
        orientation="h",
        marker_color=BLUE,
        hovertemplate="%{y}: %{x:.1f}%<extra></extra>",
    )
    fig.update_layout(
        title=f"Top uplift drivers, surrogate TreeSHAP (training-sample R² {imp.surrogate_r2.iloc[0]:.2f})",
        xaxis_title="% of attribution",
        bargap=0.25,
    )
    figs.append(("What the model relies on", style(fig, 440, legend=False), ""))

    lift = policy.expected_profit - policy.blanket_expected_profit
    tiles = [
        ("Clients scored", f"{policy.n_scored:,.0f}"),
        ("Recommended to text", f"{policy.n_send:,.0f} ({policy.n_send / policy.n_scored:.0%})"),
        ("Projected extra buyers (model-based)", f"{policy.expected_incremental_purchases:,.0f}"),
        ("Projected profit (model-based)", f"₽{policy.expected_profit:,.0f}"),
        ("Projected vs texting everyone", f"{'+' if lift >= 0 else '−'}₽{abs(lift):,.0f}"),
        ("Test Qini (champion)", f"{champ.qini_auc:+.4f}"),
        ("Top-10% uplift (test)", f"{champ.uplift_at_10:+.1%}"),
        ("Response model top-10%", f"{resp.uplift_at_10:+.1%}"),
    ]
    tiles_html = "".join(
        f"<div class='tile'><div class='k'>{html.escape(k)}</div>"
        f"<div class='v'>{html.escape(v)}</div></div>"
        for k, v in tiles
    )
    cards = "".join(
        f"<section class='card'><h2>{html.escape(h)}</h2>{fig_html(f)}"
        + (f"<p class='note'>{html.escape(note)}</p>" if note else "")
        + "</section>"
        for h, f, note in figs
    )

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Uplift Targeting Engine</title>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<style>
  :root {{ --bg:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
          --line:rgba(11,11,11,.10); }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }}
  main {{ max-width:1180px; margin:0 auto; padding:32px 16px 64px; }}
  h1 {{ font-size:28px; margin:0 0 8px; }}
  .lede {{ color:var(--ink2); max-width:820px; line-height:1.5; }}
  .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px;
           margin:24px 0; }}
  .tile {{ background:var(--surface); border:1px solid var(--line); border-radius:10px;
          padding:14px 16px; }}
  .tile .k {{ color:var(--muted); font-size:13px; }}
  .tile .v {{ font-size:24px; font-weight:600; margin-top:4px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(480px,1fr)); gap:16px; }}
  @media (max-width:560px) {{ .grid {{ grid-template-columns:1fr; }} }}
  .card {{ background:var(--surface); border:1px solid var(--line); border-radius:12px;
          padding:16px; min-width:0; overflow:hidden; }}
  .card h2 {{ font-size:16px; margin:0 0 4px; }}
  .sub {{ color:var(--ink2); font-size:13px; margin:0 0 4px; }}
  .note {{ color:var(--ink2); font-size:13px; margin:6px 0 0; }}
  footer {{ color:var(--muted); font-size:13px; margin-top:32px; }}
  a {{ color:#2a78d6; }}
</style></head><body><main>
<h1>Who should get the promo SMS?</h1>
<p class="lede">Causal uplift model on the X5 RetailHero randomised SMS experiment (400k loyalty
clients). It ranks clients by how much the message <em>changes</em> their chance of buying and
texts only where that pays for the SMS. Champion: <b>{html.escape(model_label(champ.model))}</b>,
scored on a reused historical test split of {int(champ.n_test):,} clients.</p>
<p class="lede"><b>Evidence status:</b> historical point estimates on a test split that was scored twice
(before and after a leakage fix) and reused by an audit; profit intervals withdrawn pending a corrected
bootstrap; the deployed calibrated rule has not yet been evaluated as a complete policy; profit and
buyer totals are model-based projections. See
<a href="https://github.com/NirmalKumar31/uplift-targeting-engine/blob/main/docs/AUDIT_2026-10-25.md">the audit</a>.</p>
<div class="tiles">{tiles_html}</div>
<div class="grid">{cards}</div>
<footer>Static snapshot of the Streamlit BI dashboard, built from the dbt marts by
<code>scripts/build_site.py</code>. Economics are assumptions (SMS ₽{policy.sms_cost:.0f},
{policy.margin_per_purchase:.0f} ₽ margin per extra purchase).
Source: <a href="https://github.com/NirmalKumar31/uplift-targeting-engine">github.com/NirmalKumar31/uplift-targeting-engine</a></footer>
</main>
<script>window.addEventListener('load', () =>
  document.querySelectorAll('.js-plotly-plot').forEach((g) => Plotly.Plots.resize(g)));</script>
</body></html>"""
    out = ROOT / "docs" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(page)
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
