"""Streamlit BI dashboard: `uv run streamlit run dashboard/app.py`."""

import streamlit as st

st.set_page_config(page_title="SMS Campaign Targeting", page_icon="🎯", layout="wide")

pages = {
    "Start here": [
        st.Page(
            "views/overview.py", title="Campaign summary", icon=":material/insights:", default=True
        ),
        st.Page("views/simulator.py", title="Explore campaign costs", icon=":material/tune:"),
        st.Page("views/segments.py", title="Audience groups", icon=":material/groups:"),
    ],
    "More detail": [
        st.Page("views/models.py", title="Model checks", icon=":material/leaderboard:"),
        st.Page("views/drivers.py", title="What the model uses", icon=":material/account_tree:"),
        st.Page("views/health.py", title="Data checks", icon=":material/monitor_heart:"),
        st.Page("views/clients.py", title="Contact list", icon=":material/person_search:"),
    ],
}
st.navigation(pages).run()
