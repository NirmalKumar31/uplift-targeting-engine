import plotly.graph_objects as go
import streamlit as st

from common import BLUE, MUTED, chart, load

st.title("What drives uplift?")
imp = load("mart_feature_importance").sort_values("mean_abs_contribution", ascending=False)
r2 = imp.surrogate_r2.iloc[0]
st.markdown(
    f"A LightGBM surrogate fitted to the deployed uplift scores (training-sample R² = "
    f"**{r2:.2f}**; held-out fidelity not yet measured) and exact "
    "TreeSHAP splits each client's score among the features. This describes what the *model* "
    "relies on, not what causes buying: correlated features share credit."
)

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
    title="Share of total attribution (top 15 features)",
    xaxis_title="% of mean |SHAP|",
    bargap=0.25,
)
chart(fig, 520, legend=False)

st.subheader("How the score moves with each driver")
dep = load("mart_feature_dependence")
feats = list(dict.fromkeys(f for f in imp.feature if f in set(dep.feature)))
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
    hovertemplate=f"{feature} ≈ %{{x:,.1f}}<br>%{{y:+.2f}} pp<extra></extra>",
)
fig.update_layout(
    xaxis_title=f"{feature} (median of each 5% bin)",
    yaxis_title="Contribution to uplift score (pp)",
)
chart(fig, 380, legend=False)

with st.expander("All features"):
    st.dataframe(
        imp[["feature", "share", "direction_corr", "missing_rate"]].rename(
            columns={
                "share": "attribution share",
                "direction_corr": "direction (corr)",
                "missing_rate": "missing",
            }
        ),
        hide_index=True,
        width="stretch",
    )
