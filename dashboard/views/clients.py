import streamlit as st

from common import SEGMENT_ORDER, load, pct

st.title("Send list & client lookup")
clients = load("dim_client")

c = st.columns(4)
action = c[0].multiselect("Action", ["send", "holdout", "skip"], default=["send"])
segment = c[1].multiselect("Segment", SEGMENT_ORDER, default=SEGMENT_ORDER)
gender = c[2].multiselect("Gender", ["F", "M", "U"], default=["F", "M", "U"])
search = c[3].text_input("Client id contains")

f = clients[
    clients.action.isin(action) & clients.segment.isin(segment) & clients.gender.isin(gender)
]
if search:
    f = f[f.client_id.str.contains(search.strip(), case=False)]
f = f.sort_values("uplift_rank")

k = st.columns(3)
k[0].metric("Clients", f"{len(f):,}")
k[1].metric("Expected extra purchases", f"{f.expected_uplift.sum():,.0f}")
k[2].metric("Avg expected uplift", pct(f.expected_uplift.mean() if len(f) else 0, 2, True))

cols = [
    "client_id",
    "uplift_rank",
    "uplift_decile",
    "segment",
    "action",
    "expected_uplift",
    "p_buy_without_sms",
    "p_buy_with_sms",
    "expected_profit",
    "age",
    "gender",
    "n_txn",
    "recency_days",
    "spend_total",
]
st.dataframe(
    f[cols].head(5000),
    hide_index=True,
    width="stretch",
    column_config={
        "expected_uplift": st.column_config.NumberColumn("expected uplift", format="percent"),
        "p_buy_without_sms": st.column_config.NumberColumn("P(buy) no SMS", format="percent"),
        "p_buy_with_sms": st.column_config.NumberColumn("P(buy) with SMS", format="percent"),
        "expected_profit": st.column_config.NumberColumn("expected profit", format="%.2f"),
        "spend_total": st.column_config.NumberColumn("spend total", format="%.0f"),
    },
)
st.caption(
    "Showing up to 5,000 rows, best prospects first. `holdout` clients are selected but "
    "deliberately not texted, a random control group to measure the campaign's real lift."
)
st.download_button(
    "Download filtered list (CSV)",
    f[cols].to_csv(index=False),
    file_name="send_list.csv",
    mime="text/csv",
)
