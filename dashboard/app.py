"""Streamlit BI dashboard: `uv run streamlit run dashboard/app.py`."""

import streamlit as st

st.set_page_config(page_title="Uplift Targeting Engine", page_icon="🎯", layout="wide")

pages = [
    st.Page(
        "views/overview.py", title="Executive overview", icon=":material/insights:", default=True
    ),
    st.Page("views/simulator.py", title="Campaign ROI simulator", icon=":material/tune:"),
    st.Page("views/segments.py", title="Customer segments", icon=":material/groups:"),
    st.Page("views/models.py", title="Model performance", icon=":material/leaderboard:"),
    st.Page("views/drivers.py", title="Uplift drivers", icon=":material/account_tree:"),
    st.Page("views/health.py", title="Data & model health", icon=":material/monitor_heart:"),
    st.Page("views/clients.py", title="Send list & client lookup", icon=":material/person_search:"),
]
st.navigation(pages).run()
