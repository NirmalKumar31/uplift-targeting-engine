import plotly.graph_objects as go
import streamlit as st

from common import BLUE, GRID, INK2, MUTED, ORANGE, chart, load, model_label, pct

st.title("Model performance")
board = load("mart_model_leaderboard").sort_values("qini_auc")
st.markdown(
    "Every learner is cross-fitted (5 folds) on the 160k-client development set; the "
    "champion is fixed by out-of-fold Qini **before** the 40k-client historical test split is "
    "scored. Learners share one LightGBM configuration."
)

fig = go.Figure()
lo, hi = board.noise_floor_low.iloc[0], board.noise_floor_high.iloc[0]
fig.add_vrect(
    x0=lo,
    x1=hi,
    fillcolor=GRID,
    opacity=0.6,
    line_width=0,
    annotation_text="random-ranking noise band",
    annotation_position="top left",
    annotation_font_color=MUTED,
)
colors = [
    BLUE if c else (MUTED if b else INK2)
    for c, b in zip(board.is_champion, board.is_baseline, strict=True)
]
fig.add_scatter(
    x=board.qini_auc,
    y=board.model.map(model_label),
    mode="markers",
    marker=dict(color=colors, size=11, line=dict(color="#fcfcfb", width=2)),
    error_x=dict(
        type="data",
        symmetric=False,
        array=board.qini_auc_high - board.qini_auc,
        arrayminus=board.qini_auc - board.qini_auc_low,
        color=MUTED,
        thickness=1.5,
    ),
    hovertemplate="%{y}<br>Qini %{x:+.4f}<extra></extra>",
    name="Qini AUC",
)
fig.update_layout(
    title="Out-of-fold Qini AUC with 95% bootstrap intervals",
    xaxis_title="Normalised Qini AUC (0 = random)",
)
chart(fig, 420, legend=False)
st.caption("Blue = champion, gray = baselines. The response model scores below random.")

b = board.sort_values("rank")
st.dataframe(
    b.assign(
        **{
            "Model": b.model.map(model_label),
            "Qini": b.qini_auc.map("{:+.4f}".format),
            "95% CI": [
                f"[{a:+.4f}, {c:+.4f}]"
                for a, c in zip(b.qini_auc_low, b.qini_auc_high, strict=True)
            ],
            "Uplift@10%": b.uplift_at_10.map(lambda v: pct(v, 1, True)),
            "Uplift@30%": b.uplift_at_30.map(lambda v: pct(v, 1, True)),
            "Champion's Qini lead": [
                "—" if c else f"{d:+.4f}"
                for c, d in zip(b.is_champion, b.diff_vs_champion, strict=True)
            ],
            "P(champion better)": [
                "—" if c else f"{p:.2f}"
                for c, p in zip(b.is_champion, b.p_champion_better, strict=True)
            ],
            "Fit time (5 folds)": b.fit_seconds.map("{:.0f}s".format),
        }
    ).iloc[:, -8:],
    hide_index=True,
    width="stretch",
)

left, right = st.columns(2)
with left:
    curves = load("mart_qini_curves")
    champ = board[board.is_champion].model.iloc[0]
    split = st.radio(
        "Qini curves",
        ["test", "dev_oof"],
        horizontal=True,
        format_func={"test": "Locked test set", "dev_oof": "Dev out-of-fold"}.get,
    )
    d = curves[curves.split == split]
    default = [champ, "response"]
    chosen = st.multiselect(
        "Models",
        sorted(d.model.unique(), key=model_label),
        default=[m for m in default if m in set(d.model)],
        format_func=model_label,
        max_selections=3,
    )
    fig = go.Figure()
    ref = d[d.model == (chosen[0] if chosen else champ)]
    fig.add_scatter(
        x=ref.fraction * 100,
        y=ref.random_line,
        name="Random",
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

with right:
    cal = load("mart_uplift_calibration")
    split2 = st.radio(
        "Calibration",
        ["test", "dev_oof"],
        horizontal=True,
        key="cal",
        format_func={"test": "Locked test set", "dev_oof": "Dev out-of-fold"}.get,
    )
    c = cal[cal.split == split2].sort_values("decile")
    fig = go.Figure()
    fig.add_bar(
        x=c.decile,
        y=c.observed_uplift * 100,
        name="Observed uplift",
        marker_color=BLUE,
        error_y=dict(type="data", array=1.96 * c.observed_se * 100, color=MUTED, thickness=1),
        hovertemplate="Decile %{x}: %{y:+.2f} pp<extra>observed</extra>",
    )
    fig.update_layout(
        title="Observed uplift by score decile",
        bargap=0.25,
        xaxis=dict(title="Score decile (1 = top)", dtick=1),
        yaxis_title="Treated − control purchase rate (pp)",
    )
    chart(fig, 440, legend=False)
    st.caption("A good uplift model has a falling staircase: the top deciles respond most.")
