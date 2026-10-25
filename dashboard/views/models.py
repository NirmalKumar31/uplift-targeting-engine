import plotly.graph_objects as go
import streamlit as st

from common import BLUE, GRID, INK2, MUTED, ORANGE, chart, load, model_label, pct, source_badge

st.title("Model performance")

st.header("Selection stability across outer folds")
source_badge("assessment")
sel = load("mart_assessment_selection")
folds = load("mart_assessment_folds")
piv = sel.pivot_table(index="model", columns="fold", values="inner_oof_qini")
piv.columns = [f"fold {c}" for c in piv.columns]
piv.index = piv.index.map(model_label)
st.dataframe(piv.style.format("{:+.4f}"), width="stretch")
st.caption(
    "Inner out-of-fold Qini each candidate scored inside each outer training set; the "
    "champion of each fold was "
    + ", ".join(f"fold {r.fold}: {model_label(r.champion)}" for r in folds.itertuples())
    + ". Validation Qini of the fitted champion by fold: "
    + ", ".join(f"{q:+.4f}" for q in folds.validation_qini_raw)
    + "."
)

st.header("Calibration: predicted vs observed effect")
cal = load("mart_assessment_calibration")
pooled = cal[cal.fold == "pooled"].sort_values("bin")
fig = go.Figure()
fig.add_bar(
    x=pooled.bin,
    y=pooled.observed_uplift * 100,
    name="Observed uplift (validation)",
    marker_color=BLUE,
    error_y=dict(type="data", array=1.96 * pooled.observed_se * 100, color=MUTED, thickness=1),
    hovertemplate="Bin %{x}: observed %{y:+.2f} pp<extra></extra>",
)
fig.add_scatter(
    x=pooled.bin,
    y=pooled.predicted_effect * 100,
    mode="lines+markers",
    name="Calibrated prediction",
    line=dict(color=ORANGE, width=2),
    marker=dict(size=8),
    hovertemplate="Bin %{x}: predicted %{y:+.2f} pp<extra></extra>",
)
fig.update_layout(
    title="Pooled outer-fold validation, 10 bins of calibrated effect",
    xaxis=dict(title="Calibrated-effect bin (1 = lowest)", dtick=1),
    yaxis_title="Uplift (pp)",
    bargap=0.25,
)
chart(
    fig,
    400,
    note="Each customer's prediction comes from a policy fitted and calibrated "
    "without their fold. Bars are ±1.96 SE of the observed difference.",
)
tr = load("mart_assessment_transfer")
st.dataframe(
    tr.assign(
        **{
            "Fold": tr.fold,
            "Champion": tr.champion.map(model_label),
            "KS (inner OOF vs refit scores)": tr.ks_statistic_oof_vs_refit.map("{:.3f}".format),
            "Eligible share: inner OOF": tr.eligible_share_inner_oof.map(lambda v: pct(v, 1)),
            "Eligible share: validation": tr.eligible_share_validation.map(lambda v: pct(v, 1)),
        }
    ).iloc[:, -5:],
    hide_index=True,
    width="stretch",
)
st.caption(
    "Transfer check: the calibrator is fitted on out-of-fold scores but applied to a "
    "refitted model. Similar score distributions and eligible shares support that "
    "transfer; a large gap would mean the calibrated threshold selects a different "
    "population than it was fitted on."
)

st.header("Leaderboard on the selection data")
source_badge("oof")
board = load("mart_model_leaderboard").sort_values("qini_auc")
fig = go.Figure()
fig.add_vrect(
    x0=board.noise_band_low.iloc[0],
    x1=board.noise_band_high.iloc[0],
    fillcolor=GRID,
    opacity=0.6,
    line_width=0,
    annotation_text="random-ranking range",
    annotation_position="top left",
    annotation_font_color=MUTED,
)
fig.add_scatter(
    x=board.qini_auc,
    y=board.model.map(model_label),
    mode="markers",
    marker=dict(
        color=[
            BLUE if c else (MUTED if b else INK2)
            for c, b in zip(board.is_champion, board.is_baseline, strict=True)
        ],
        size=11,
        line=dict(color="#fcfcfb", width=2),
    ),
    error_x=dict(
        type="data",
        symmetric=False,
        array=board.qini_auc_high - board.qini_auc,
        arrayminus=board.qini_auc - board.qini_auc_low,
        color=MUTED,
        thickness=1.5,
    ),
    hovertemplate="%{y}<br>Qini %{x:+.4f}<extra></extra>",
)
fig.update_layout(
    title="Inner out-of-fold Qini AUC, 95% intervals (fixed scores)",
    xaxis_title="Normalised Qini AUC (0 = random)",
)
chart(
    fig,
    440,
    legend=False,
    note="Blue = deployed champion, gray = baselines. The champion "
    "was chosen on these same numbers, so its lead is optimistic.",
)
b = board.sort_values("qini_auc", ascending=False)
st.dataframe(
    b.assign(
        **{
            "Model": b.model.map(model_label),
            "Qini [95%]": [
                f"{q:+.4f} [{lo:+.4f}, {hi:+.4f}]"
                for q, lo, hi in zip(b.qini_auc, b.qini_auc_low, b.qini_auc_high, strict=True)
            ],
            "Uplift@10%": b.uplift_at_10.map(lambda v: pct(v, 1, True)),
            "Uplift@30%": b.uplift_at_30.map(lambda v: pct(v, 1, True)),
            "Champion's lead": [
                "—" if c else f"{d:+.4f} [{lo:+.4f}, {hi:+.4f}]"
                for c, d, lo, hi in zip(
                    b.is_champion,
                    b.qini_lead_of_reference,
                    b.qini_lead_low,
                    b.qini_lead_high,
                    strict=True,
                )
            ],
            "Share of draws champion ahead": [
                "—" if c else f"{f:.2f}"
                for c, f in zip(b.is_champion, b.frac_reference_ahead, strict=True)
            ],
        }
    ).iloc[:, -6:],
    hide_index=True,
    width="stretch",
)

st.header("Qini curves")
curves = load("mart_qini_curves")
sources = sorted(curves.source.unique())
src = st.radio("Evaluation source", sources, horizontal=True)
d = curves[curves.source == src]
models = sorted(d.model.unique(), key=model_label)
default = [
    x
    for x in models
    if x in {"champion", "response"} or x == board[board.is_champion].model.iloc[0]
][:3]
chosen = st.multiselect(
    "Models", models, default=default or models[:2], format_func=model_label, max_selections=3
)
fig = go.Figure()
if chosen:
    ref = d[d.model == chosen[0]]
    fig.add_scatter(
        x=ref.fraction * 100,
        y=ref.random_line,
        name="Random line",
        mode="lines",
        line=dict(color=MUTED, width=1.5),
        hoverinfo="skip",
    )
for name, color in zip(chosen, [BLUE, ORANGE, "#1baf7a"], strict=False):
    c = d[d.model == name]
    fig.add_scatter(
        x=c.fraction * 100,
        y=c.incremental_buyers,
        name=model_label(name),
        mode="lines",
        line=dict(color=color, width=2),
        hovertemplate="Top %{x:.0f}%: %{y:,.0f}<extra></extra>",
    )
fig.update_layout(xaxis_title="Share texted (%)", yaxis_title="Incremental buyers")
chart(fig, 380)
st.caption(f"Source: {src}.")
