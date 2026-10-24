import plotly.graph_objects as go
import streamlit as st

from common import BLUE, MUTED, SEGMENT_COLORS, SEGMENT_ORDER, chart, load, money, pct, query

st.title("Customer segments")
st.markdown(
    "- **Persuadable**: the SMS raises their purchase chance by more than the break-even.\n"
    "- **Sure thing**: likely to buy anyway, little to gain.\n"
    "- **Lost cause**: unlikely to buy either way.\n"
    "- **Do not disturb**: the SMS is predicted to *lower* their purchase chance."
)

seg = (
    query("""
    select segment, sum(n_clients) n, sum(expected_incremental_purchases) inc,
           sum(n_clients * avg_expected_uplift) / sum(n_clients) uplift,
           sum(n_clients * avg_p_buy_without_sms) / sum(n_clients) p0
    from {mart_segment_summary} group by segment
""")
    .set_index("segment")
    .reindex(SEGMENT_ORDER)
    .fillna(0)
)
cols = st.columns(4)
for col, (name, r) in zip(cols, seg.iterrows(), strict=True):
    col.metric(
        name,
        f"{int(r.n):,}",
        f"avg uplift {pct(r.uplift, 2, True)}",
        delta_color="off",
        delta_arrow="off",
    )

left, right = st.columns(2)
with left:
    mix = query("""
        select age_band, segment, sum(n_clients) n from {mart_segment_summary} group by all
    """)
    mix["share"] = mix.n / mix.groupby("age_band").n.transform("sum")
    bands = ["18-24", "25-34", "35-44", "45-54", "55-64", "65+", "Unknown"]
    fig = go.Figure()
    for s in SEGMENT_ORDER:
        d = mix[mix.segment == s].set_index("age_band").reindex(bands)
        fig.add_bar(
            y=bands,
            x=d.share,
            name=s,
            orientation="h",
            marker=dict(color=SEGMENT_COLORS[s], line=dict(color="#fcfcfb", width=2)),
            hovertemplate="%{y}: %{x:.0%} " + s + "<extra></extra>",
        )
    fig.update_layout(
        title="Segment mix by age band",
        barmode="stack",
        xaxis=dict(tickformat=".0%", title="Share of clients"),
        yaxis=dict(autorange="reversed"),
    )
    chart(fig, 400)

with right:
    dec = load("mart_decile_summary")
    fig = go.Figure()
    fig.add_bar(
        x=dec.uplift_decile,
        y=dec.avg_expected_uplift * 100,
        marker_color=BLUE,
        name="Expected uplift",
        hovertemplate="Decile %{x}: %{y:+.2f} pp<extra></extra>",
    )
    fig.update_layout(
        title="Expected uplift by predicted decile (all clients)",
        xaxis=dict(title="Uplift decile (1 = most persuadable)", dtick=1),
        yaxis_title="Expected uplift (pp)",
        bargap=0.25,
    )
    chart(fig, 400, legend=False)

st.subheader("What the experiment actually shows")
st.caption(
    "Observed SMS effect (texted minus not-texted purchase rate) within each slice of the "
    "randomised experiment, no model involved. Bars show 95% intervals."
)
cate = load("mart_experiment_cate")
dim = st.segmented_control(
    "Slice by", [d for d in cate.dimension.unique() if d != "Overall"], default="Predicted decile"
)
overall = cate[cate.dimension == "Overall"].observed_uplift.iloc[0]
d = cate[cate.dimension == (dim or "Predicted decile")].sort_values("level")
fig = go.Figure()
fig.add_bar(
    x=d.level,
    y=d.observed_uplift * 100,
    marker_color=BLUE,
    name="Observed uplift",
    error_y=dict(type="data", array=1.96 * d.std_error * 100, color=MUTED, thickness=1),
    customdata=d.n_clients,
    hovertemplate="%{x}<br>%{y:+.2f} pp (n=%{customdata:,})<extra></extra>",
)
fig.add_hline(
    y=overall * 100,
    line=dict(color=MUTED, width=1.5),
    annotation_text=f"All clients {overall * 100:+.2f} pp",
    annotation_font_color=MUTED,
    annotation_position="top left",
)
fig.update_layout(yaxis_title="Observed uplift (pp)", bargap=0.3)
chart(fig, 380, legend=False)

st.subheader("Decile economics")
policy = load("mart_campaign_policy").iloc[0]
t = dec.assign(
    **{
        "Decile": dec.uplift_decile,
        "Clients": dec.n_clients.map("{:,}".format),
        "Expected uplift": dec.avg_expected_uplift.map(lambda v: pct(v, 2, True)),
        "P(buy) without SMS": dec.avg_p_buy_without_sms.map(lambda v: pct(v, 0)),
        "Extra purchases if texted": dec.expected_incremental_purchases.map("{:,.0f}".format),
        "Profit if texted": dec.expected_profit_if_texted.map(money),
        "Share sent": dec.share_sent.map(lambda v: pct(v, 0)),
        "Avg age": dec.avg_age.map("{:.0f}".format),
        "Avg visits": dec.avg_visits.map("{:.0f}".format),
    }
)
st.dataframe(t.iloc[:, -9:], hide_index=True, width="stretch")
