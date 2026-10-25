import plotly.graph_objects as go
import streamlit as st

from common import BLUE, CRITICAL, GRID, MUTED, WARNING, chart, load

st.title("Data & model health")

st.subheader("Covariate balance between experiment arms")
bal = load("mart_experiment_balance")
n_bad = int((~bal.balanced).sum())
st.markdown(
    f"**{len(bal) - n_bad} of {len(bal)} checks** have |standardised mean difference| < 0.1, "
    "covering numeric means (non-missing values), every category level and every feature's "
    "missingness rate. This is consistent with the random SMS assignment documented for the "
    "dataset. It is a diagnostic on measured covariates and cannot prove randomisation or rule "
    "out imbalance in unmeasured ones."
)
bal = bal.assign(
    label=bal.feature
    + bal.level.fillna("").map(lambda v: f" = {v}" if v else "")
    + " ("
    + bal.check
    + ")"
).sort_values("smd")
fig = go.Figure()
fig.add_vrect(x0=-0.1, x1=0.1, fillcolor=GRID, opacity=0.5, line_width=0)
fig.add_scatter(
    x=bal.smd,
    y=bal.label,
    mode="markers",
    marker=dict(color=[BLUE if b else CRITICAL for b in bal.balanced], size=7),
    hovertemplate="%{y}: SMD %{x:+.4f}<extra></extra>",
)
fig.update_layout(
    xaxis=dict(title="Standardised mean difference (treated − control)", range=[-0.12, 0.12])
)
chart(fig, max(500, 14 * len(bal)), legend=False)

st.subheader("Data quality rules applied before features")
st.dataframe(
    load("mart_data_quality").rename(columns={"check_name": "Rule", "n_rows": "Rows"}),
    hide_index=True,
    width="stretch",
)

st.subheader("Population comparison (PSI)")
drift = load("mart_drift").sort_values("psi", ascending=False)
st.caption(
    f"{drift.comparison.iloc[0].capitalize()}; {drift.role.iloc[0]}. It does not monitor a "
    "new campaign over time."
)
counts = drift.status.value_counts()
c = st.columns(3)
c[0].metric("Low (PSI < 0.10)", int(counts.get("low", 0)))
c[1].metric("Moderate (0.10-0.25)", int(counts.get("moderate", 0)))
c[2].metric("High (≥ 0.25)", int(counts.get("high", 0)))
top = drift.head(15).iloc[::-1]
fig = go.Figure()
fig.add_bar(
    y=top.feature,
    x=top.psi,
    orientation="h",
    marker_color=[{"low": MUTED, "moderate": WARNING, "high": CRITICAL}[s] for s in top.status],
    customdata=top.status,
    hovertemplate="%{y}: PSI %{x:.4f} (%{customdata})<extra></extra>",
)
fig.update_layout(title="Highest PSI (top 15)", xaxis_title="PSI", bargap=0.25)
chart(fig, 440, legend=False)

st.subheader("Historical test-split access log")
st.dataframe(load("mart_test_access_log"), hide_index=True, width="stretch")

st.subheader("Runs in this release (lineage)")
runs = load("mart_pipeline_runs")
st.dataframe(
    runs[
        [
            c
            for c in (
                "stage",
                "run_id",
                "status",
                "git_commit",
                "git_dirty",
                "source_fp",
                "config_hash",
                "dataset_fp",
                "feature_build_id",
                "started_at",
            )
            if c in runs.columns
        ]
    ],
    hide_index=True,
    width="stretch",
)
