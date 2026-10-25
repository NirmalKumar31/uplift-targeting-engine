"""Real-browser checks of the static site and the Streamlit dashboard (Playwright + Chrome).

    uv run --group browser python scripts/browser_check.py --site docs/index.html \
        [--streamlit http://localhost:8501] [--report docs/evidence/browser_check.json]

Static site, desktop and phone width: every Plotly plot has traces with finite numeric data,
hovering a point produces a tooltip, no console errors, no horizontal page overflow.
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

from playwright.sync_api import sync_playwright

PLOT_JS = """
() => [...document.querySelectorAll('.js-plotly-plot')].map((gd, i) => {
  const traces = (gd._fullData || []).map(t => {
    const nums = a => a == null ? [] : Array.from(a).filter(v => typeof v === 'number');
    const xs = nums(t.x), ys = nums(t.y);
    const vals = ys.length ? ys : xs;
    return {type: t.type, n: Math.max(xs.length, ys.length, (t.x||[]).length, (t.y||[]).length),
            numeric: vals.length, finite: vals.filter(Number.isFinite).length,
            hover: t.hoverinfo !== 'skip'};
  });
  return {index: i, traces};
})
"""

HOVER_JS = """
(i) => {
  const gd = document.querySelectorAll('.js-plotly-plot')[i];
  const k = gd._fullData.findIndex(t => t.hoverinfo !== 'skip' && (t.x||t.y||[]).length);
  if (k < 0) return null;
  Plotly.Fx.hover(gd, [{curveNumber: k, pointNumber: 0}]);
  return gd.querySelectorAll('.hoverlayer .hovertext').length;
}
"""

PAGES = ["", "simulator", "segments", "models", "drivers", "health", "clients"]


def check_site(browser, site: Path) -> dict:
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
        hovers = [page.evaluate(HOVER_JS, i) for i in range(len(plots))]
        overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        bad = [
            p
            for p in plots
            if not p["traces"] or any(t["n"] and t["finite"] == 0 for t in p["traces"])
        ]
        out[label] = {
            "n_plots": len(plots),
            "plots_without_numeric_data": bad,
            "hover_tooltips": hovers,
            "console_errors": errors,
            "horizontal_overflow_px": overflow,
            "ok": bool(plots)
            and not bad
            and all(h for h in hovers if h is not None)
            and not errors
            and overflow <= 1,
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
    ap.add_argument("--streamlit")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()
    report = {"checked_at": datetime.now(UTC).isoformat()}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        report["browser"] = f"Chrome {browser.version}"
        if args.site:
            report["site"] = check_site(browser, args.site)
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
