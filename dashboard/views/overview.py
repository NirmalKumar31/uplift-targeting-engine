import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import (
    BLUE,
    MUTED,
    ORANGE,
    chart,
    interval,
    load,
    model_label,
    money,
    pct,
    results,
    source_badge,
)

res = results()
econ = res["economics"]
cur = "₽" if econ["currency"] == "RUB" else econ["currency"] + " "
A = res["assessment"]
pol = pd.DataFrame(A["policies"]).set_index("policy")
dif = pd.DataFrame(A["differences"])

st.title("Who should get the promo SMS?")
st.markdown(
    "A causal **uplift model** estimates how much an SMS *changes* each customer's chance of "
    "buying. The deployed rule texts a customer when the calibrated effect × the assumed value of "
    f"an additional buyer ({cur}{econ['margin_per_additional_buyer']:.0f}) exceeds the SMS cost "
    f"({cur}{econ['sms_cost']:.2f}), i.e. above a **{pct(econ['break_even_uplift'], 2)}** "
    f"break-even uplift. Release `{res['release_id']}`, policy `{res['policy_version']}`."
)
st.info(
    "**Evidence status.** Three different kinds of numbers appear below and are not "
    "interchangeable: an internal outer-fold validation of the whole policy, a historical test "
    "split that has been reused, and model-based projections. None is an independent "
    "confirmation; economics are assumptions. Intervals cover customer sampling for fixed fitted "
    "models only.",
    icon="ℹ️",
)

st.header("1 · Is the deployed policy better than the alternatives?")
source_badge("assessment")
dep = pol.loc["deployed"]
c = st.columns(4)
c[0].metric(
    "Deployed policy value / 1,000",
    money(dep.value_per_1000, cur),
    interval(dep.value_low, dep.value_high),
    delta_color="off",
    delta_arrow="off",
)
c[1].metric(
    "Customers it texts",
    pct(dep.selected_share, 1),
    f"{dep.n_selected_treated:,.0f} treated / {dep.n_selected_control:,.0f} control",
    delta_color="off",
    delta_arrow="off",
)
for col, ref in zip(c[2:], ["blanket", "response_optimized"], strict=True):
    row = dif.query("policy == 'deployed' and reference == @ref").iloc[0]
    col.metric(
        f"Advantage vs {model_label(ref).lower()}",
        money(row.difference_per_1000, cur),
        interval(row.difference_low, row.difference_high),
        delta_color="off",
        delta_arrow="off",
    )

left, right = st.columns([3, 2])
with left:
    order = ["deployed", "response_optimized", "random_expected", "blanket", "no_send"]
    p = pol.reindex([o for o in order if o in pol.index])
    fig = go.Figure()
    fig.add_bar(
        x=[model_label(i) for i in p.index],
        y=p.value_per_1000,
        marker_color=[
            BLUE if i == "deployed" else (ORANGE if "response" in i else MUTED) for i in p.index
        ],
        error_y=dict(
            type="data",
            symmetric=False,
            color=MUTED,
            thickness=1,
            array=(p.value_high - p.value_per_1000).fillna(0),
            arrayminus=(p.value_per_1000 - p.value_low).fillna(0),
        ),
        customdata=p.selected_share,
        hovertemplate="%{x}<br>%{y:,.0f} per 1,000 (texts %{customdata:.0%})<extra></extra>",
    )
    fig.add_hline(y=0, line=dict(color=MUTED, width=1))
    fig.update_layout(
        title=f"Estimated incremental value per 1,000 customers ({cur.strip()}), 95% intervals",
        yaxis_title="Value vs texting nobody",
        bargap=0.35,
    )
    chart(
        fig,
        380,
        legend=False,
        note="Random (expected) = the texted share chosen at random; its value is that share "
        "of texting everyone. Text nobody is 0 by definition.",
    )
with right:
    st.markdown("**Paired differences (same bootstrap draws)**")
    show = dif.assign(
        Comparison=lambda d: d.policy.map(model_label) + " − " + d.reference.map(model_label),
        Difference=lambda d: d.difference_per_1000.map(lambda v: money(v, cur)),
        Interval=lambda d: [
            interval(a, b, lambda v: money(v, cur))
            for a, b in zip(d.difference_low, d.difference_high, strict=True)
        ],
        **{"Share of draws > 0": lambda d: d.frac_positive.map("{:.2f}".format)},
    )
    st.dataframe(
        show[show.policy == "deployed"][
            ["Comparison", "Difference", "Interval", "Share of draws > 0"]
        ],
        hide_index=True,
        width="stretch",
    )
    lo, hi = A["fold_value_range_deployed"]
    st.caption(
        f"Fold-by-fold deployed value ranged {money(lo, cur)} to {money(hi, cur)} per "
        f"1,000 (folds share training data; descriptive only). Champion by fold: "
        f"{', '.join(model_label(x) for x in A['champions_by_fold'])}. "
        "'Share of draws > 0' is a bootstrap fraction, not a probability."
    )

st.header("2 · Historical reused test (2026-10-24 bundle)")
source_badge("historical")
H = res["historical_test"]
hm = pd.DataFrame(H["models"]).set_index("model")
hd = pd.DataFrame(H["differences"])
c = st.columns(4)
ch = hm.loc["champion"]
c[0].metric(
    "Champion Qini",
    f"{ch.qini_auc:+.4f}",
    f"[{ch.qini_auc_low:+.4f}, {ch.qini_auc_high:+.4f}]",
    delta_color="off",
    delta_arrow="off",
)
c[1].metric(
    "Top-10% uplift",
    pct(ch.uplift_at_10, 1, True),
    f"[{pct(ch.uplift_at_10_low, 1, True)}, {pct(ch.uplift_at_10_high, 1, True)}]",
    delta_color="off",
    delta_arrow="off",
)
row = hd.query("policy == 'champion_at_41pct' and reference == 'blanket'").iloc[0]
c[2].metric(
    "41% policy − text everyone",
    money(row.difference_per_1000, cur),
    interval(row.difference_low, row.difference_high),
    delta_color="off",
    delta_arrow="off",
)
c[3].metric(
    "Times this split was scored",
    f"{len(H['access_log'])} logged",
    "+ audit reuse",
    delta_color="off",
    delta_arrow="off",
)
curves = load("mart_qini_curves").query("source == 'historical reused test'")
fig = go.Figure()
ref = curves[curves.model == "champion"]
fig.add_scatter(
    x=ref.fraction * 100,
    y=ref.random_line,
    mode="lines",
    name="Random line",
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
        hovertemplate="Top %{x:.0f}%: %{y:,.0f} additional buyers<extra></extra>",
    )
fig.update_layout(
    title="Qini curve on the historical reused test split",
    xaxis_title="Share of customers texted (%)",
    yaxis_title="Incremental buyers",
)
chart(fig, 360)

st.header("3 · Deployment projection (model-based)")
source_badge("projection")
P = res["deployment_projection"]
c = st.columns(4)
c[0].metric("Customers scored", f"{P['n_scored']:,}")
c[1].metric("Eligible (effect clears cost)", f"{P['n_eligible']:,}")
c[2].metric(
    "To text / held out",
    f"{P['n_send']:,} / {P['n_holdout']:,}",
    f"{P['holdout_share']:.0%} random holdout",
    delta_color="off",
    delta_arrow="off",
)
c[3].metric(
    "Projected additional buyers (texted only)",
    f"{P['projected_additional_buyers_sent']:,.0f}",
    f"projected value {money(P['projected_value_sent'], cur)}",
    delta_color="off",
    delta_arrow="off",
)
st.caption(
    P["status"] + ". The projected value of texting everyone under the same assumptions "
    f"is {money(P['projected_value_blanket'], cur)}."
)
