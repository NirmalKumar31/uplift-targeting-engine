import numpy as np
import plotly.graph_objects as go
import streamlit as st

from common import BLUE, GOOD, MUTED, chart, load, money

curve = load("mart_profit_curve").sort_values("depth")
policy = load("mart_campaign_policy").iloc[0]
cur = "₽" if policy.currency == "RUB" else policy.currency + " "

st.title("Campaign ROI simulator")
st.markdown(
    "Change the economics and see how deep down the uplift ranking it pays to text. "
    "Uplift by depth is measured out-of-fold on all 200k experiment clients; only the "
    "money changes."
)

c = st.columns(3)
sms = c[0].slider("Cost per SMS", 0.25, 15.0, float(policy.sms_cost), 0.25, format=f"{cur}%.2f")
margin = c[1].slider(
    "Profit per extra purchase",
    10.0,
    400.0,
    float(round(policy.margin_per_purchase)),
    5.0,
    format=f"{cur}%.0f",
)
base = c[2].number_input("Client base", 1_000, 10_000_000, int(policy.n_scored), 10_000)

depth = curve.depth.to_numpy()
uplift = curve.uplift.to_numpy()
profit = base * depth * (margin * uplift - sms)
best = int(np.nanargmax(profit))
best_depth, best_profit = (depth[best], profit[best]) if profit[best] > 0 else (0.0, 0.0)
blanket = profit[-1]
k = st.columns(4)
k[0].metric("Profit-maximising depth", f"{best_depth:.0%}")
k[1].metric("Clients to text", f"{int(best_depth * base):,}")
k[2].metric("Expected profit", money(best_profit, cur))
k[3].metric(
    "vs texting everyone",
    money(best_profit - blanket, cur),
    f"everyone earns {money(blanket, cur)}",
    delta_color="off",
    delta_arrow="off",
)

fig = go.Figure()
scale = base / 1000
fig.add_scatter(
    x=np.r_[depth, depth[::-1]] * 100,
    y=np.r_[
        base * depth * (margin * curve.uplift_high - sms),
        (base * depth * (margin * curve.uplift_low - sms))[::-1],
    ],
    fill="toself",
    fillcolor="rgba(42,120,214,0.12)",
    line=dict(width=0),
    name="95% interval",
    hoverinfo="skip",
)
fig.add_scatter(
    x=depth * 100,
    y=profit,
    mode="lines",
    name="Expected profit",
    line=dict(color=BLUE, width=2),
    hovertemplate="Text top %{x:.0f}%<br>Profit %{y:,.0f}<extra></extra>",
)
fig.add_hline(y=0, line=dict(color=MUTED, width=1))
if best_profit > 0:
    fig.add_scatter(
        x=[best_depth * 100],
        y=[best_profit],
        mode="markers",
        name="Optimum",
        marker=dict(color=GOOD, size=11, line=dict(color="#fcfcfb", width=2)),
        hovertemplate="Best: top %{x:.0f}%<br>%{y:,.0f}<extra></extra>",
    )
fig.update_layout(
    title=f"Campaign profit by targeting depth ({cur.strip()})",
    xaxis_title="Share of clients texted, highest uplift first (%)",
    yaxis_title="Profit",
)
chart(fig, 420)
st.caption(
    f"Break-even uplift = SMS cost ÷ margin = {sms / margin:.2%}. Keep texting while the "
    "next clients' uplift is above it. The shaded band is the bootstrap uncertainty of the "
    "measured uplift."
)

st.subheader("Sensitivity: optimal depth by break-even uplift")
sens = load("mart_policy_sensitivity")
sens = sens.assign(
    **{
        "Break-even uplift": sens.break_even_uplift.map("{:.1%}".format),
        "Best depth": sens.best_depth.map("{:.0%}".format),
        f"Profit per 1,000 clients ({cur.strip()})": sens.profit_per_1000.map("{:,.0f}".format),
    }
)[["Break-even uplift", "Best depth", f"Profit per 1,000 clients ({cur.strip()})"]]
st.dataframe(sens, hide_index=True, width="stretch")
