"""Build a complete miniature release from the deterministic raw fixture (no network, ~1 min).

    uv run python scripts/build_fixture_release.py OUT_DIR

OUT_DIR gets raw/, a warehouse, releases/ (with an ACTIVE release) and marts/ - the same layout
the real pipeline produces. Used by CI for the browser check and the API container test.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from conftest import write_fixture_config  # noqa: E402
from raw_fixture import make_historical_snapshot, make_raw  # noqa: E402
from uplift_engine import cli  # noqa: E402

out = Path(sys.argv[1]).resolve()
files = make_raw(out / "raw")
make_historical_snapshot(out / "snapshot", out / "snapshot_manifest.json")
cli.run(config=write_fixture_config(out, files))
print(f"fixture release ready under {out}")
