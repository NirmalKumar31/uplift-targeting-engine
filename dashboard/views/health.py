import plotly.graph_objects as go
import streamlit as st

from common import BLUE, CRITICAL, GOOD, GRID, MUTED, WARNING, chart, load

st.title("Data & model health")

bal = load("mart_experiment_balance").sort_values("smd")
st.subheader("Experiment validity: covariate balance")
n_bad = int((~bal.balanced).sum())
st.markdown(
    f"{'✅' if n_bad == 0 else '⚠️'} **{len(bal) - n_bad} of {len(bal)} features balanced** "
    "(|standardised mean difference| < 0.1 between texted and not-texted clients): "
    "randomisation worked, so differences in purchase rates are caused by the SMS."
)
fig = go.Figure()
fig.add_vrect(x0=-0.1, x1=0.1, fillcolor=GRID, opacity=0.5, line_width=0)
fig.add_scatter(
    x=bal.smd,
    y=bal.feature,
    mode="markers",
    marker=dict(color=[BLUE if b else CRITICAL for b in bal.balanced], size=8),
    hovertemplate="%{y}: SMD %{x:+.4f}<extra></extra>",
)
fig.update_layout(xaxis=dict(title="Standardised mean difference", range=[-0.12, 0.12]))
chart(fig, 760, legend=False)

st.subheader("Population drift: experiment clients vs the rest of the base")
drift = load("mart_drift").sort_values("psi", ascending=False)
icon = {"ok": "✅ ok", "warn": "⚠️ warn", "fail": "⛔ fail"}
counts = drift.status.value_counts()
c = st.columns(3)
c[0].metric("Stable (PSI < 0.10)", int(counts.get("ok", 0)))
c[1].metric("Warning (0.10-0.25)", int(counts.get("warn", 0)))
c[2].metric("Severe (≥ 0.25)", int(counts.get("fail", 0)))
top = drift.head(15).iloc[::-1]
fig = go.Figure()
fig.add_bar(
    y=top.feature,
    x=top.psi,
    orientation="h",
    marker_color=[{"ok": GOOD, "warn": WARNING, "fail": CRITICAL}[s] for s in top.status],
    customdata=top.status.map(icon),
    hovertemplate="%{y}: PSI %{x:.3f} (%{customdata})<extra></extra>",
)
fig.add_vline(x=0.1, line=dict(color=MUTED, width=1))
fig.update_layout(
    title="Highest population stability index (top 15)", xaxis_title="PSI", bargap=0.25
)
chart(fig, 460, legend=False)
st.caption(
    "The model is trained on the 200k experiment clients and applied to all 400k. Low PSI "
    "means the clients it will score look like the clients it learned from."
)

st.subheader("Pipeline runs")
runs = load("mart_pipeline_runs").sort_values("started_at", ascending=False)
st.dataframe(runs, hide_index=True, width="stretch")
