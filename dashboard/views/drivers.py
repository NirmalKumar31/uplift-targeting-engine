import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import BLUE, MUTED, chart, load

st.title("What drives the uplift score?")
fid = load("mart_surrogate_fidelity")
overall = fid[fid.decile == "all"].set_index("split")
held = overall.loc["held-out rows"]
train = overall.loc["surrogate training rows"]
st.markdown(
    "This page attributes the champion's **raw uplift score**, not the calibrated effect or the "
    "profit decision. A LightGBM surrogate is fitted to the scores on 70% of a sample, and "
    "TreeSHAP of the surrogate is computed on the other 30%. SHAP describes what the model "
    "relies on, not what causes buying, and correlated features share credit."
)
c = st.columns(3)
c[0].metric(
    "Surrogate fidelity, held-out R²",
    f"{held.r2:.3f}",
    f"training rows R² {train.r2:.3f}",
    delta_color="off",
    delta_arrow="off",
)
c[1].metric("Held-out RMSE (score units)", f"{held.rmse:.4f}")
c[2].metric("Held-out rows", f"{int(held.n):,}")

exact = load("mart_shap_exact_check")
if exact.feature.notna().any():
    err = exact.max_additivity_error.iloc[0]
    st.caption(
        f"Exact check: for the S-learner champion, treatment-on minus treatment-off "
        f"TreeSHAP contributions add up to the model's log-odds treatment effect "
        f"(max additivity error {err:.1e}). That exact attribution is on the log-odds "
        "scale, so the chart below uses the surrogate on the score scale."
    )

imp = load("mart_feature_importance").sort_values("mean_abs_contribution", ascending=False)
top = imp.head(15).iloc[::-1]
fig = go.Figure()
fig.add_bar(
    y=top.feature,
    x=top.share * 100,
    orientation="h",
    marker_color=BLUE,
    customdata=top.direction_corr,
    hovertemplate="%{y}: %{x:.1f}% of attribution<br>direction corr %{customdata:+.2f}"
    "<extra></extra>",
)
fig.update_layout(
    title="Share of mean |SHAP| (held-out rows, top 15)",
    xaxis_title="% of attribution",
    bargap=0.25,
)
chart(fig, 520, legend=False)

err_by = fid[(fid.split == "held-out rows") & (fid.decile != "all")].copy()
err_by["decile"] = err_by.decile.astype(int)
fig = go.Figure()
fig.add_bar(
    x=err_by.sort_values("decile").decile,
    y=err_by.sort_values("decile").mae,
    marker_color=MUTED,
    hovertemplate="Score decile %{x}: MAE %{y:.4f}<extra></extra>",
)
fig.update_layout(
    title="Surrogate error by score decile (held-out)",
    xaxis=dict(title="Score decile (1 = lowest)", dtick=1),
    yaxis_title="Mean absolute error",
    bargap=0.25,
)
chart(fig, 300, legend=False)

st.subheader("How the score moves with each driver")
dep = load("mart_feature_dependence")
feats = [f for f in imp.feature if f in set(dep.feature)]
feature = st.selectbox("Feature", feats)
d = dep[dep.feature == feature].sort_values("value")
fig = go.Figure()
fig.add_hline(y=0, line=dict(color=MUTED, width=1))
fig.add_scatter(
    x=d.value,
    y=d.contribution * 100,
    mode="lines+markers",
    line=dict(color=BLUE, width=2),
    marker=dict(size=8, color=BLUE),
    customdata=d.n,
    hovertemplate=f"{feature} ≈ %{{x:,.1f}}<br>%{{y:+.2f}} pp (n=%{{customdata}})<extra></extra>",
)
fig.update_layout(
    xaxis_title=f"{feature} (median of each 5% bin)", yaxis_title="Contribution to raw score (pp)"
)
chart(fig, 380, legend=False)
with st.expander("All features"):
    st.dataframe(
        pd.DataFrame(imp[["feature", "share", "direction_corr", "missing_rate"]]),
        hide_index=True,
        width="stretch",
    )
