import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import BLUE, GOOD, MUTED, chart, load, money, pct, results, source_badge

curve = load("mart_profit_curve").sort_values("depth")
res = results()
econ = res["economics"]
cur = "₽" if econ["currency"] == "RUB" else econ["currency"] + " "
P = res["deployment_projection"]

st.title("Campaign ROI simulator")
st.markdown(
    "Change the assumed economics and see how deep down the uplift ranking it is estimated to "
    "pay to text. The uplift at each depth is **observed** in the experiment (treated − control) "
    "among customers ranked by out-of-fold scores; only the money changes."
)
source_badge("oof")

c = st.columns(4)
sms = c[0].slider("Cost per SMS", 0.25, 15.0, float(econ["sms_cost"]), 0.25, format=f"{cur}%.2f")
margin = c[1].slider(
    "Value of one additional buyer",
    10.0,
    400.0,
    float(round(econ["margin_per_additional_buyer"])),
    5.0,
    format=f"{cur}%.0f",
)
base = c[2].number_input("Customer base", 1_000, 10_000_000, int(P["n_scored"]), 10_000)
holdout = c[3].slider(
    "Holdout share (not texted)", 0.0, 0.5, float(P["holdout_share"]), 0.01, format="%.2f"
)

depth = curve.depth.to_numpy()
texted = base * depth * (1 - holdout)


def value(u):
    return texted * (margin * np.asarray(u) - sms)


v, v_lo, v_hi = value(curve.uplift), value(curve.uplift_low), value(curve.uplift_high)
i = int(np.nanargmax(v))
best_depth, best_value = (depth[i], v[i]) if v[i] > 0 else (0.0, 0.0)
blanket = v[-1]
k = st.columns(4)
k[0].metric(
    "Estimated best depth",
    f"{best_depth:.0%}",
    "text nobody" if best_depth == 0 else f"break-even {pct(sms / margin, 2)}",
    delta_color="off",
    delta_arrow="off",
)
k[1].metric(
    "Customers texted at that depth",
    f"{int(best_depth * base * (1 - holdout)):,}",
    f"+ {int(best_depth * base * holdout):,} held out",
    delta_color="off",
    delta_arrow="off",
)
k[2].metric(
    "Estimated value at that depth",
    money(best_value, cur),
    "vs text nobody = 0",
    delta_color="off",
    delta_arrow="off",
)
k[3].metric("Estimated value, text everyone", money(blanket, cur))

fig = go.Figure()
fig.add_scatter(
    x=np.r_[depth, depth[::-1]] * 100,
    y=np.r_[v_hi, v_lo[::-1]],
    fill="toself",
    fillcolor="rgba(42,120,214,0.12)",
    line=dict(width=0),
    name="Pointwise 95% band",
    hoverinfo="skip",
)
fig.add_scatter(
    x=depth * 100,
    y=v,
    mode="lines",
    name="Estimated value",
    line=dict(color=BLUE, width=2),
    hovertemplate="Text top %{x:.0f}%<br>%{y:,.0f}<extra></extra>",
)
fig.add_hline(y=0, line=dict(color=MUTED, width=1))
if best_value > 0:
    fig.add_scatter(
        x=[best_depth * 100],
        y=[best_value],
        mode="markers",
        name="Estimated best depth",
        marker=dict(color=GOOD, size=11, line=dict(color="#fcfcfb", width=2)),
        hovertemplate="Top %{x:.0f}%: %{y:,.0f}<extra></extra>",
    )
fig.update_layout(
    title=f"Estimated campaign value by targeting depth ({cur.strip()})",
    xaxis_title="Share of customers selected, highest uplift score first (%)",
    yaxis_title="Value vs texting nobody",
)
chart(
    fig,
    420,
    note=(
        "The band is pointwise for each depth and fixed scores; it does not cover the "
        "uncertainty of picking the best depth from this same noisy curve, so the marked optimum "
        "is an optimistic estimate. Holdout customers are selected but not texted, so they cost "
        "nothing and add no "
        "value here."
    ),
)

st.subheader("Sensitivity at the current settings")
grid = sorted({round(sms / margin, 4), *[0.005, 0.01, 0.02, 0.03, 0.04, 0.05, 0.075, 0.1]})
rows = []
for be in grid:
    val = texted * margin * (curve.uplift.to_numpy() - be)
    j = int(np.nanargmax(val))
    ok = val[j] > 0
    rows.append(
        {
            "Break-even uplift": pct(be, 2)
            + (" (current)" if be == round(sms / margin, 4) else ""),
            "SMS cost at this margin": money(be * margin, cur),
            "Estimated best depth": f"{depth[j]:.0%}" if ok else "0% (text nobody)",
            "Estimated value": money(val[j] if ok else 0.0, cur),
        }
    )
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
st.caption(
    "Recomputed from the sliders above (margin, base and holdout share). The deployed "
    "policy uses a calibrated per-customer threshold rather than a fixed depth; its "
    "validated value is on the overview page."
)
