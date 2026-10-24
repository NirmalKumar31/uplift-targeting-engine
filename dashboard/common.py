"""Shared data access, palette and chart styling for the BI dashboard."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

MARTS = Path(__file__).resolve().parents[1] / "data" / "marts"

# Validated categorical palette (fixed order) + chrome; see the dataviz reference palette.
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
MUTED, GRID, AXIS, INK, INK2 = "#898781", "#e1e0d9", "#c3c2b7", "#0b0b0b", "#52514e"
GOOD, CRITICAL, WARNING = "#0ca30c", "#d03b3b", "#fab219"
SEGMENT_COLORS = {
    "Persuadable": BLUE,
    "Sure thing": ORANGE,
    "Lost cause": AQUA,
    "Do not disturb": YELLOW,
}
SEGMENT_ORDER = list(SEGMENT_COLORS)


SHORT = {
    "class_transformation": "CT",
    "dr_learner": "DR",
    "x_learner": "X",
    "s_learner": "S",
    "t_learner": "T",
}
LONG = {
    "class_transformation": "Class transformation",
    "dr_learner": "DR-learner",
    "x_learner": "X-learner",
    "s_learner": "S-learner",
    "t_learner": "T-learner",
    "response": "Response model",
    "random": "Random",
}


@st.cache_data(show_spinner=False)
def load(name: str) -> pd.DataFrame:
    path = MARTS / f"{name}.parquet"
    if not path.exists():
        st.error(f"Missing mart `{name}`. Run `make pipeline` (or `uv run ute marts`) first.")
        st.stop()
    return duckdb.sql(f"SELECT * FROM '{path.as_posix()}'").df()


@st.cache_data(show_spinner=False)
def query(sql: str) -> pd.DataFrame:
    """Run SQL against the mart parquet files; refer to a mart as {mart_name}."""
    import re

    resolved = re.sub(
        r"\{(\w+)\}", lambda m: f"'{(MARTS / (m.group(1) + '.parquet')).as_posix()}'", sql
    )
    return duckdb.sql(resolved).df()


def model_label(name: str) -> str:
    if name.startswith("ensemble:"):
        return "Ensemble (" + " + ".join(SHORT.get(p, p) for p in name[9:].split("+")) + ")"
    return LONG.get(name, name.replace("_", " ").capitalize())


def style(fig: go.Figure, height: int = 380, legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=36, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#fcfcfb",
        font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=INK2, size=13),
        title=dict(font=dict(color=INK, size=15), y=0.98, yanchor="top", x=0, xanchor="left"),
        showlegend=legend,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.01, x=0, title_text="", traceorder="normal"
        ),
        hoverlabel=dict(bgcolor="#ffffff", font_color=INK, bordercolor=GRID),
    )
    axis = dict(
        gridcolor=GRID,
        linecolor=AXIS,
        zerolinecolor=AXIS,
        tickfont=dict(color=MUTED),
        title_font=dict(color=INK2),
    )
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    return fig


def pct(x: float, digits: int = 1, sign: bool = False) -> str:
    return f"{x:+.{digits}%}" if sign else f"{x:.{digits}%}"


def money(x: float, currency: str = "₽") -> str:
    return f"{currency}{x:,.0f}" if x >= 0 else f"−{currency}{abs(x):,.0f}"


def caption(text: str) -> None:
    st.caption(text)


def chart(fig: go.Figure, height: int = 380, legend: bool = True, note: str = "") -> None:
    """Render a figure with its title as page text (no title/legend collisions in Streamlit)."""
    title = fig.layout.title.text
    if title:
        st.markdown(f"**{title}**")
    style(fig, height, legend)
    fig.update_layout(title=None, margin=dict(t=40 if legend else 12))
    st.plotly_chart(fig, width="stretch")
    if note:
        st.caption(note)
