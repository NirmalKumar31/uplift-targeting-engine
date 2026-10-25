"""The browser oracle must reject missing charts, empty data traces and missing hover."""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import browser_check as bc  # noqa: E402


def _trace(name="a", n=5, finite=5, decorative=False):
    return {
        "type": "bar",
        "name": name,
        "decorative": decorative,
        "n": n,
        "finite": finite,
        "hover": True,
    }


def _plots(**overrides):
    plots = {cid: [_trace()] for cid in bc.REQUIRED_CHARTS}
    plots.update({k.replace("_", "-"): v for k, v in overrides.items()})
    return [{"id": cid, "traces": traces} for cid, traces in plots.items() if traces is not None]


GOOD_HOVER = dict.fromkeys(bc.REQUIRED_CHARTS, 1)


def test_complete_page_passes():
    assert bc.evaluate_plots(_plots(), GOOD_HOVER, bc.REQUIRED_CHARTS)["ok"]


def test_missing_required_chart_fails():
    v = bc.evaluate_plots(_plots(drivers=None), GOOD_HOVER, bc.REQUIRED_CHARTS)
    assert not v["ok"] and v["missing_charts"] == ["drivers"]


@pytest.mark.parametrize("trace", [_trace(n=0, finite=0), _trace(n=8, finite=0)])
def test_empty_or_non_numeric_data_trace_fails(trace):
    v = bc.evaluate_plots(_plots(calibration=[_trace(), trace]), GOOD_HOVER, bc.REQUIRED_CHARTS)
    assert not v["ok"] and "calibration" in v["empty_data_traces"]


def test_decorative_traces_are_exempt_but_data_traces_are_required():
    ok = bc.evaluate_plots(
        _plots(calibration=[_trace(), _trace("rand", 0, 0, True)]), GOOD_HOVER, bc.REQUIRED_CHARTS
    )
    assert ok["ok"]
    only_decorative = bc.evaluate_plots(
        _plots(calibration=[_trace("rand", 5, 5, True)]), GOOD_HOVER, bc.REQUIRED_CHARTS
    )
    assert not only_decorative["ok"]


def test_missing_hover_fails():
    hover = GOOD_HOVER | {"group-mix": 0}
    v = bc.evaluate_plots(_plots(), hover, bc.REQUIRED_CHARTS)
    assert not v["ok"] and v["charts_without_hover"] == ["group-mix"]


def test_historical_chart_required_only_when_available(tmp_path):
    p = tmp_path / "results.json"
    p.write_text('{"historical_test": {"available": false, "reason": "x"}}')
    assert bc.HISTORICAL_CHART not in bc.required_charts(p)
    p.write_text('{"historical_test": {"available": true}}')
    assert bc.HISTORICAL_CHART in bc.required_charts(p)


CHROME = (
    bool(shutil.which("google-chrome"))
    or Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").exists()
)


@pytest.mark.skipif(
    importlib.util.find_spec("playwright") is None or not CHROME,
    reason="needs Playwright and Chrome",
)
def test_real_browser_rejects_a_broken_page(tmp_path):
    import plotly.graph_objects as go
    from playwright.sync_api import sync_playwright

    good = go.Figure(go.Bar(x=["a", "b"], y=[1, 2])).to_html(
        div_id="policy-value", full_html=False, include_plotlyjs="cdn"
    )
    empty = go.Figure(go.Bar(x=[], y=[])).to_html(
        div_id="calibration", full_html=False, include_plotlyjs=False
    )
    page = tmp_path / "index.html"
    page.write_text(f"<!doctype html><html><body>{good}{empty}</body></html>")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        report = bc.check_site(browser, page, bc.REQUIRED_CHARTS)
        browser.close()
    desktop = report["desktop"]
    assert not desktop["ok"]
    assert "calibration" in desktop["empty_data_traces"]
    assert {"decile-effect", "group-mix", "drivers"} <= set(desktop["missing_charts"])
