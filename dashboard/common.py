"""Shared data access, labels and chart styling for the dashboard pages."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

EXPORT = Path(__file__).resolve().parents[1] / "data" / "marts"


def source() -> tuple[Path, str]:
    """Marts directory and release id to serve: $UTE_MARTS_DIR, else the active release, else
    the committed export in data/marts.
    """
    if os.environ.get("UTE_MARTS_DIR"):
        marts = Path(os.environ["UTE_MARTS_DIR"])
    else:
        marts = EXPORT
        try:
            from uplift_engine.config import load_config
            from uplift_engine.release import active_release

            marts = active_release(load_config())[1] / "marts"
        except Exception:  # no active release: use the committed export
            pass
    path = marts / "RELEASE.json"
    if not path.exists():
        st.error(f"No release found in `{marts}`. Run `uv run ute run` first.")
        st.stop()
    return marts, json.loads(path.read_text())["release_id"]


# Categorical palette, fixed order.
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
MUTED, GRID, AXIS, INK, INK2 = "#898781", "#e1e0d9", "#c3c2b7", "#0b0b0b", "#52514e"
GOOD, CRITICAL, WARNING = "#0ca30c", "#d03b3b", "#fab219"
GROUPS = [
    "Predicted persuadable",
    "Predicted sure thing",
    "Predicted lost cause",
    "Predicted do-not-disturb",
]
GROUP_COLORS = dict(zip(GROUPS, [BLUE, ORANGE, AQUA, YELLOW], strict=True))
AGE_BANDS = ["14-17", "18-24", "25-34", "35-44", "45-54", "55-64", "65+", "Unknown"]

SOURCES = {
    "assessment": (
        "Outer-fold retrospective validation",
        "Whole policy procedure fitted without each customer's fold, all 200k labelled "
        "customers. Internal validation on data used during earlier exploration: not "
        "independent confirmation.",
    ),
    "historical": (
        "Historical reused test",
        "The 2026-10-24 bundle on its 40,008-customer test split, which was scored "
        "repeatedly and reused by the audit.",
    ),
    "projection": (
        "Model-based projection",
        "Sums of calibrated model effects under the assumed economics for the whole "
        "base. A scenario, not an observed campaign result.",
    ),
    "oof": (
        "All-labelled inner OOF",
        "Out-of-fold scores of the deployed procedure on all labelled customers: the data "
        "the champion was selected on, so optimistic for it.",
    ),
    "experiment": (
        "Observed experiment slice",
        "Raw treated-minus-control difference in the randomised experiment; no model.",
    ),
}


def release_id() -> str:
    return source()[1]


def has_mart(name: str) -> bool:
    return (source()[0] / f"{name}.parquet").exists()


@st.cache_data(show_spinner=False)
def _load(name: str, marts: str, rid: str) -> pd.DataFrame:
    return duckdb.sql(f"SELECT * FROM '{(Path(marts) / f'{name}.parquet').as_posix()}'").df()


def load(name: str) -> pd.DataFrame:
    marts, rid = source()
    if not (marts / f"{name}.parquet").exists():
        st.error(f"Mart `{name}` is missing from release {rid}.")
        st.stop()
    return _load(name, str(marts), rid)


@st.cache_data(show_spinner=False)
def _results(marts: str, rid: str) -> dict:
    return json.loads((Path(marts) / "results.json").read_text())


def results() -> dict:
    marts, rid = source()
    return _results(str(marts), rid)


@st.cache_data(show_spinner=False)
def _query(sql: str, marts: str, rid: str) -> pd.DataFrame:
    resolved = re.sub(
        r"\{(\w+)\}",
        lambda m: f"'{(Path(marts) / (m.group(1) + '.parquet')).as_posix()}'",
        sql,
    )
    return duckdb.sql(resolved).df()


def query(sql: str) -> pd.DataFrame:
    """SQL over the mart parquet files; refer to a mart as {mart_name}."""
    marts, rid = source()
    return _query(sql, str(marts), rid)


def source_badge(key: str) -> None:
    title, text = SOURCES[key]
    st.caption(f"**Source: {title}.** {text}")


def model_label(name: str) -> str:
    short = {
        "class_transformation": "CT",
        "dr_learner": "DR",
        "x_learner": "X",
        "s_learner": "S",
        "t_learner": "T",
    }
    long = {
        "class_transformation": "Class transformation",
        "dr_learner": "DR-learner",
        "x_learner": "X-learner",
        "s_learner": "S-learner",
        "t_learner": "T-learner",
        "response": "Response model",
        "random": "Random",
        "random_draw": "Random (one draw)",
        "champion": "Historical champion (S-learner)",
        "champion_pooled": "Champion",
        "deployed": "Deployed policy",
        "response_optimized": "Response model (own best share)",
        "blanket": "Text everyone",
        "no_send": "Text nobody",
        "random_expected": "Random, same share (expected)",
    }
    if name.startswith("ensemble:"):
        return "Ensemble (" + " + ".join(short.get(p, p) for p in name[9:].split("+")) + ")"
    return long.get(name, name.replace("_", " ").capitalize())


def style(fig: go.Figure, height: int = 380, legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=40 if legend else 12, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#fcfcfb",
        font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=INK2, size=13),
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
        automargin=True,
    )
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    return fig


def chart(fig: go.Figure, height: int = 380, legend: bool = True, note: str = "") -> None:
    """Render a figure with its title as page text."""
    title = fig.layout.title.text
    if title:
        st.markdown(f"**{title}**")
    style(fig, height, legend)
    fig.update_layout(title=None)
    st.plotly_chart(fig, width="stretch")
    if note:
        st.caption(note)


def pct(x: float, digits: int = 1, sign: bool = False) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{x:+.{digits}%}" if sign else f"{x:.{digits}%}"


def money(x: float, currency: str = "₽") -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{currency}{x:,.0f}" if x >= 0 else f"−{currency}{abs(x):,.0f}"


def send_rows(df: pd.DataFrame) -> pd.DataFrame:
    return df[df.action == "send"]


def interval(lo: float, hi: float, fmt=money) -> str:
    return f"[{fmt(lo)}, {fmt(hi)}]"
