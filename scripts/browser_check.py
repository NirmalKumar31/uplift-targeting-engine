"""Real-browser checks of the static site and the Streamlit dashboard (Playwright + Chrome).

    uv run --group browser python scripts/browser_check.py --site docs/index.html \
        [--streamlit http://localhost:8501] [--report docs/evidence/browser_check.json]

Static site, desktop and phone width: every *required* chart (decided from results.json, not from
the page) is present; each has non-decorative traces with finite numeric data and a working hover
tooltip; no console errors; no horizontal page overflow. Decorative traces (e.g. the random line)
are tagged `meta="decorative"` by build_site.py and exempt from the data check.
Streamlit (optional): every page loads without a Streamlit exception and renders its charts or
tables; the client search accepts regex metacharacters as literal text.
Exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REQUIRED_CHARTS = ["policy-value", "calibration", "decile-effect", "group-mix", "drivers"]
HISTORICAL_CHART = "hist-qini"

PLOT_JS = """
() => [...document.querySelectorAll('.js-plotly-plot')].map((gd) => {
  const traces = (gd._fullData || []).map(t => {
    const vals = a => a == null ? [] : Array.from(a);
    const finite = a => a.filter(v => typeof v === 'number' && Number.isFinite(v)).length;
    const xs = vals(t.x), ys = vals(t.y);
    // the value axis is whichever axis carries numbers (y for vertical, x for horizontal bars)
    return {type: t.type, name: t.name, decorative: t.meta === 'decorative',
            n: Math.max(xs.length, ys.length), finite: Math.max(finite(xs), finite(ys)),
            hover: t.hoverinfo !== 'skip'};
  });
  return {id: gd.id, traces};
})
"""

HOVER_JS = """
(id) => {
  const gd = document.getElementById(id);
  const k = gd._fullData.findIndex(t => t.meta !== 'decorative' && t.hoverinfo !== 'skip'
                                        && (t.x || t.y || []).length);
  if (k < 0) return 0;
  Plotly.Fx.hover(gd, [{curveNumber: k, pointNumber: 0}]);
  return gd.querySelectorAll('.hoverlayer .hovertext').length;
}
"""


def required_charts(results_path: Path | None) -> list[str]:
    """The charts the page must contain, decided from results.json - not from the page."""
    if results_path is None:
        return list(REQUIRED_CHARTS)
    res = json.loads(results_path.read_text())
    hist = res.get("historical_test", {}).get("available", True)
    return [*REQUIRED_CHARTS, *([HISTORICAL_CHART] if hist else [])]


def evaluate_plots(plots: list[dict], hovers: dict[str, int], required: list[str]) -> dict:
    """Pure verdict on what the browser measured (unit-testable without a browser)."""
    by_id = {p["id"]: p for p in plots}
    missing = [c for c in required if c not in by_id]
    empty = {}
    for cid in required:
        for t in by_id.get(cid, {}).get("traces", []):
            if not t["decorative"] and (t["n"] == 0 or t["finite"] == 0):
                empty.setdefault(cid, []).append(t["name"])
        if cid in by_id and not any(not t["decorative"] for t in by_id[cid]["traces"]):
            empty.setdefault(cid, []).append("<no data traces>")
    no_hover = [c for c in required if c in by_id and not hovers.get(c)]
    return {
        "missing_charts": missing,
        "empty_data_traces": empty,
        "charts_without_hover": no_hover,
        "ok": not missing and not empty and not no_hover,
    }


PAGES = ["", "simulator", "segments", "models", "drivers", "health", "clients"]


def check_site(browser, site: Path, required: list[str]) -> dict:
    out = {}
    for label, viewport in (("desktop", (1280, 900)), ("phone", (390, 844))):
        page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
        errors = []
        page.on(
            "console", lambda m, errors=errors: errors.append(m.text) if m.type == "error" else None
        )
        page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
        page.goto(site.resolve().as_uri())
        page.wait_for_selector(".js-plotly-plot", timeout=60000)
        page.wait_for_timeout(1500)
        plots = page.evaluate(PLOT_JS)
        hovers = {p["id"]: page.evaluate(HOVER_JS, p["id"]) for p in plots if p["id"]}
        overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        verdict = evaluate_plots(plots, hovers, required)
        out[label] = {
            "charts": [p["id"] for p in plots],
            "required": required,
            **verdict,
            "hover_tooltips": hovers,
            "console_errors": errors,
            "horizontal_overflow_px": overflow,
            "ok": verdict["ok"] and not errors and overflow <= 1,
        }
        page.close()
    return out


def check_streamlit(browser, url: str) -> dict:
    out = {}
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    for name in PAGES:
        page.goto(f"{url}/{name}")
        page.wait_for_selector("[data-testid='stAppViewContainer']", timeout=60000)
        page.wait_for_function(
            "() => document.querySelectorAll('.js-plotly-plot, [data-testid=stDataFrame]')"
            ".length > 0",
            timeout=90000,
        )
        page.wait_for_timeout(1500)
        out[name or "overview"] = {
            "exceptions": page.locator("[data-testid='stException']").count(),
            "plots": page.locator(".js-plotly-plot").count(),
            "tables": page.locator("[data-testid='stDataFrame']").count(),
        }
    page.goto(f"{url}/clients")
    page.wait_for_selector("[data-testid='stDataFrame']", timeout=90000)
    box = page.get_by_label("Client id contains (literal text)")
    search = {}
    for text in ("[", "(", "."):
        box.fill(text)
        box.press("Enter")
        page.wait_for_timeout(2500)
        search[text] = page.locator("[data-testid='stException']").count()
    out["client_search_exceptions"] = search
    out["ok"] = all(
        v["exceptions"] == 0 and (v["plots"] or v["tables"])
        for k, v in out.items()
        if isinstance(v, dict) and "exceptions" in v
    ) and not any(search.values())
    page.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", type=Path)
    ap.add_argument(
        "--results",
        type=Path,
        help="results.json deciding the required charts (default: next to --site's "
        "marts is unknown, so only the always-required charts)",
    )
    ap.add_argument("--streamlit")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    report = {"checked_at": datetime.now(UTC).isoformat()}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        report["browser"] = f"Chrome {browser.version}"
        if args.site:
            report["site"] = check_site(browser, args.site, required_charts(args.results))
        if args.streamlit:
            report["streamlit"] = check_streamlit(browser, args.streamlit)
        browser.close()
    ok = all(v.get("ok", True) for k, v in report.get("site", {}).items()) and report.get(
        "streamlit", {}
    ).get("ok", True)
    report["ok"] = ok
    text = json.dumps(report, indent=2)
    print(text)
    if args.report:
        args.report.write_text(text + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
