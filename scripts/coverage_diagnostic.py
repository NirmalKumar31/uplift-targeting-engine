"""Run the bootstrap coverage diagnostic and record it under docs/evidence/.

uv run python scripts/coverage_diagnostic.py
"""

import json
from pathlib import Path

from uplift_engine.evaluation.diagnostics import coverage

ROOT = Path(__file__).resolve().parents[1]
results = {
    "heterogeneous_effect": coverage(seed=1),
    "null_effect": coverage(effect=0.0, heterogeneity=0.0, seed=2),
}
out = ROOT / "docs" / "evidence" / "bootstrap_coverage_diagnostic.json"
out.write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps(results, indent=2))
