import plotly.graph_objects as go
import streamlit as st

from common import (
    AGE_BANDS,
    BLUE,
    GROUP_COLORS,
    GROUPS,
    MUTED,
    chart,
    load,
    money,
    pct,
    query,
    source_badge,
)

st.title("Customer groups")
st.markdown(
    "Groups are **predicted business labels** from the model's calibrated effect and baseline "
    "purchase probability. They are not observed individual response types: nobody's "
    "counterfactual is observed.\n\n"
    "- **Predicted persuadable:** calibrated effect above the break-even uplift.\n"
    "- **Predicted sure thing:** below break-even, and P(buy without SMS) ≥ 0.7.\n"
    "- **Predicted lost cause:** below break-even, and a lower baseline.\n"
    "- **Predicted do-not-disturb:** calibrated effect ≤ 0."
)
source_badge("projection")

grp = (
    query("""
    select predicted_group, sum(n_clients) n,
           sum(n_clients * avg_calibrated_effect) / sum(n_clients) effect,
           sum(n_send) n_send, sum(n_holdout) n_holdout
    from {mart_segment_summary} group by predicted_group
""")
    .set_index("predicted_group")
    .reindex(GROUPS)
    .fillna(0)
)
cols = st.columns(4)
for col, (name, r) in zip(cols, grp.iterrows(), strict=True):
    col.metric(
        name,
        f"{int(r.n):,}",
        f"avg calibrated effect {pct(r.effect, 2, True)}",
        delta_color="off",
        delta_arrow="off",
    )
if grp.loc["Predicted do-not-disturb", "n"] == 0:
    st.caption(
        "No customer has a calibrated effect ≤ 0 under this release, so the "
        "do-not-disturb group is empty."
    )

left, right = st.columns(2)
with left:
    mix = query(
        "select age_band, predicted_group, sum(n_clients) n "
        "from {mart_segment_summary} group by all"
    )
    totals = mix.groupby("age_band").n.sum()
    mix["share"] = mix.n / mix.age_band.map(totals)
    bands = [b for b in AGE_BANDS if b in totals.index]  # omit bands with no customers
    fig = go.Figure()
    for g in GROUPS:
        d = mix[mix.predicted_group == g].set_index("age_band").reindex(bands)
        fig.add_bar(
            y=bands,
            x=d.share.fillna(0),
            name=g,
            orientation="h",
            marker=dict(color=GROUP_COLORS[g], line=dict(color="#fcfcfb", width=2)),
            customdata=d.n.fillna(0),
            hovertemplate="%{y}: %{x:.0%} (%{customdata:,} customers)<extra>" + g + "</extra>",
        )
    fig.update_layout(
        title="Predicted group mix by age band",
        barmode="stack",
        xaxis=dict(tickformat=".0%", title="Share of customers in the band"),
        yaxis=dict(autorange="reversed"),
    )
    chart(fig, 420)
    st.caption(
        "Band sizes: "
        + ", ".join(f"{b} {int(totals.get(b, 0)):,}" for b in AGE_BANDS)
        + ". Ages outside 14-100 in the raw file are shown as Unknown."
    )

with right:
    dec = load("mart_decile_summary")
    fig = go.Figure()
    fig.add_bar(
        x=dec.uplift_decile,
        y=dec.avg_calibrated_effect * 100,
        marker_color=BLUE,
        hovertemplate="Decile %{x}: %{y:+.2f} pp<extra></extra>",
    )
    fig.update_layout(
        title="Average calibrated effect by score decile (all customers)",
        xaxis=dict(title="Score decile (1 = highest)", dtick=1),
        yaxis_title="Calibrated effect (pp)",
        bargap=0.25,
    )
    chart(fig, 420, legend=False)

st.subheader("What the experiment shows, slice by slice")
source_badge("experiment")
cate = load("mart_experiment_cate")
dims = [d for d in cate.dimension.unique() if d != "Overall"]
dim = st.segmented_control("Slice by", dims, default="Predicted decile (OOF)") or dims[0]
overall = cate[cate.dimension == "Overall"].observed_uplift.iloc[0]
d = cate[cate.dimension == dim].sort_values("level")
fig = go.Figure()
fig.add_bar(
    x=d.level,
    y=d.observed_uplift * 100,
    marker_color=BLUE,
    error_y=dict(type="data", array=1.96 * d.std_error * 100, color=MUTED, thickness=1),
    customdata=d[["n_treated", "n_control"]],
    hovertemplate="%{x}<br>%{y:+.2f} pp<br>%{customdata[0]:,} treated, "
    "%{customdata[1]:,} control<extra></extra>",
)
fig.add_hline(
    y=overall * 100,
    line=dict(color=MUTED, width=1.5),
    annotation_text=f"All customers {overall * 100:+.2f} pp",
    annotation_font_color=MUTED,
    annotation_position="top left",
)
fig.update_layout(yaxis_title="Observed uplift (pp)", bargap=0.3)
chart(
    fig,
    380,
    legend=False,
    note=(
        "Bars are ±1.96 standard errors. Differences between slices are descriptive: slices "
        "are not pre-registered, overlap with each other, and correlate with other features, "
        "so they do not show that a demographic causes responsiveness."
    ),
)

st.subheader("Score deciles")
st.dataframe(
    dec.assign(
        **{
            "Decile": dec.uplift_decile,
            "Customers": dec.n_clients.map("{:,}".format),
            "Avg calibrated effect": dec.avg_calibrated_effect.map(lambda v: pct(v, 2, True)),
            "P(buy) without SMS": dec.avg_p_buy_without_sms.map(lambda v: pct(v, 0)),
            "Share eligible": dec.share_eligible.map(lambda v: pct(v, 0)),
            "Texted / held out": [
                f"{a:,} / {b:,}" for a, b in zip(dec.n_send, dec.n_holdout, strict=True)
            ],
            "Projected value (texted only)": dec.projected_value_sent.fillna(0).map(money),
            "Avg age": dec.avg_age.map("{:.0f}".format),
        }
    ).iloc[:, -8:],
    hide_index=True,
    width="stretch",
)
