import streamlit as st

from common import GROUPS, load, pct, send_rows, source_badge

st.title("Send list & client lookup")
source_badge("projection")
clients = load("dim_client")

c = st.columns(4)
action = c[0].multiselect("Campaign action", ["send", "holdout", "ineligible"], default=["send"])
group = c[1].multiselect("Predicted group", GROUPS, default=GROUPS)
gender = c[2].multiselect("Gender", ["F", "M", "U"], default=["F", "M", "U"])
search = c[3].text_input("Client id contains (literal text)")

f = clients[
    clients.action.isin(action) & clients.predicted_group.isin(group) & clients.gender.isin(gender)
]
if search:
    f = f[f.client_id.str.contains(search.strip(), case=False, regex=False, na=False)]
f = f.sort_values("uplift_rank")

texted = send_rows(f)
k = st.columns(3)
k[0].metric("Customers in view", f"{len(f):,}")
k[1].metric("Of which texted (action = send)", f"{len(texted):,}")
k[2].metric(
    "Projected additional buyers, texted only",
    f"{texted.calibrated_effect.sum():,.1f}",
    f"avg calibrated effect {pct(texted.calibrated_effect.mean(), 2, True)}"
    if len(texted)
    else "no texted customers in view",
    delta_color="off",
    delta_arrow="off",
)

cols = [
    "client_id",
    "action",
    "uplift_rank",
    "uplift_decile",
    "predicted_group",
    "calibrated_effect",
    "p_buy_without_sms",
    "expected_value_if_texted",
    "age",
    "age_band",
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
        "calibrated_effect": st.column_config.NumberColumn("calibrated effect", format="percent"),
        "p_buy_without_sms": st.column_config.NumberColumn("P(buy) no SMS", format="percent"),
        "expected_value_if_texted": st.column_config.NumberColumn("value if texted", format="%.2f"),
        "spend_total": st.column_config.NumberColumn("spend total", format="%.0f"),
    },
)
st.caption(
    "Up to 5,000 rows, highest scores first. `holdout` customers are eligible but are "
    "deliberately not texted (randomised control group). Projections assume the "
    "calibrated effects and assumed economics hold."
)

d1, d2 = st.columns(2)
d1.download_button(
    "Download send list (action = send only)",
    texted[["client_id", "policy_version"]].to_csv(index=False),
    file_name="send_list.csv",
    mime="text/csv",
    help="Only customers whose frozen campaign action is 'send'; holdout is never included.",
)
d2.download_button(
    "Download this view for analysis (not a send list)",
    f[cols].to_csv(index=False),
    file_name="analysis_view.csv",
    mime="text/csv",
)
