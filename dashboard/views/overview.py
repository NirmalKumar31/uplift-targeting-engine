import plotly.graph_objects as go
import streamlit as st

from common import BLUE, MUTED, ORANGE, chart, load, model_label, money, pct

policy = load("mart_campaign_policy").iloc[0]
test = load("mart_test_scorecard")
champ = test[test.role == "champion"].iloc[0]
resp = test[test.model == "response"].iloc[0]
cur = "₽" if policy.currency == "RUB" else policy.currency + " "

st.title("Who should get the promo SMS?")
st.markdown(
    f"A causal **uplift model** ranks every loyalty-card holder by how much the SMS *changes* "
    f"their chance of buying, then texts only the clients where that change pays for the message. "
    f"Champion: **{model_label(champ.model)}**, judged on a locked test set of "
    f"{int(champ.n_test):,} clients from a randomised experiment."
)

c = st.columns(4)
c[0].metric("Clients scored", f"{int(policy.n_scored):,}")
c[1].metric(
    "Recommended to text",
    f"{int(policy.n_send):,}",
    f"{policy.n_send / policy.n_scored:.0%} of base",
    delta_color="off",
    delta_arrow="off",
)
c[2].metric(
    "Expected extra purchases",
    f"{policy.expected_incremental_purchases:,.0f}",
    f"vs {policy.ate * policy.n_scored:,.0f} texting everyone",
    delta_color="off",
    delta_arrow="off",
)
lift = policy.expected_profit - policy.blanket_expected_profit
c[3].metric(
    "Expected campaign profit",
    money(policy.expected_profit, cur),
    f"{money(lift, cur)} vs texting everyone",
)

c = st.columns(4)
c[0].metric(
    "Test Qini (champion)",
    f"{champ.qini_auc:+.4f}",
    f"95% CI {champ.qini_auc_low:+.4f} to {champ.qini_auc_high:+.4f}",
    delta_color="off",
    delta_arrow="off",
)
c[1].metric(
    "Uplift in top 10%",
    pct(champ.uplift_at_10, 1, True),
    f"{champ.uplift_at_10 / champ.ate:.1f}× the average effect",
    delta_color="off",
    delta_arrow="off",
)
c[2].metric(
    "Response-model top 10%",
    pct(resp.uplift_at_10, 1, True),
    "targets likely buyers, not persuadables",
    delta_color="off",
    delta_arrow="off",
)
c[3].metric(
    "Break-even uplift",
    pct(policy.break_even_uplift, 2),
    f"SMS {cur}{policy.sms_cost:.2f} ÷ margin {cur}{policy.margin_per_purchase:.0f}",
    delta_color="off",
    delta_arrow="off",
)

left, right = st.columns(2)
with left:
    curves = load("mart_qini_curves")
    curves = curves[curves.split == "test"]
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
            hovertemplate="Top %{x:.0f}% texted<br>%{y:,.0f} extra buyers<extra></extra>",
        )
    fig.update_layout(
        title="Qini curve on the locked test set",
        xaxis_title="Share of clients texted (%)",
        yaxis_title="Incremental buyers",
    )
    chart(fig)
    st.caption(
        "Above the gray line = better than texting at random. The response model "
        "hugs or dips below it: it spends SMS on people who would buy anyway."
    )

with right:
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
            hovertemplate="%{x}: %{y:.0f} extra purchases per 1,000<extra>"
            + model_label(name)
            + "</extra>",
        )
    fig.add_hline(
        y=1000 * champ.ate,
        line=dict(color=MUTED, width=1.5),
        annotation_text=f"Random: {1000 * champ.ate:.0f}",
        annotation_font_color=MUTED,
        annotation_position="top left",
    )
    fig.update_layout(
        title="Extra purchases per 1,000 clients texted (test)",
        barmode="group",
        bargap=0.3,
        bargroupgap=0.08,
        yaxis_title="Incremental purchases / 1,000",
    )
    chart(fig)
    st.caption(
        "Bars show 95% bootstrap intervals. Gray line: the average effect of the SMS, "
        "what texting a random group would earn."
    )
