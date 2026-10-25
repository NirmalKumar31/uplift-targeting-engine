"""Freeze the current model outputs as a versioned local audit snapshot with a hash manifest.

    uv run python scripts/snapshot_evidence.py <label> [--log FILE ...]

Copies warehouse tables (ml.*, meta.*), model artifacts, exported marts and the static site into
snapshots/<label>/ (gitignored: binaries and client-level data stay out of git) and writes
docs/evidence/<label>_manifest.json (committed) with every file's SHA-256 and row counts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("--log", nargs="*", default=[], help="pipeline console logs to preserve")
    args = ap.parse_args()

    out = ROOT / "snapshots" / args.label
    if out.exists():
        raise SystemExit(f"{out} exists; snapshots are immutable")
    (out / "tables").mkdir(parents=True)

    rows = {}
    con = duckdb.connect(str(ROOT / "data" / "warehouse.duckdb"), read_only=True)
    for schema, table in con.execute(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema IN ('ml', 'meta') ORDER BY 1, 2"
    ).fetchall():
        dest = out / "tables" / f"{schema}.{table}.parquet"
        con.execute(f"COPY {schema}.{table} TO '{dest.as_posix()}' (FORMAT parquet)")
        rows[f"{schema}.{table}"] = con.execute(
            f"SELECT count(*) FROM {schema}.{table}"
        ).fetchone()[0]
    con.close()

    for src, name in [(ROOT / "artifacts", "artifacts"), (ROOT / "data" / "marts", "marts")]:
        if src.exists():
            shutil.copytree(src, out / name)
    (out / "site").mkdir()
    shutil.copy2(ROOT / "docs" / "index.html", out / "site" / "index.html")
    if args.log:
        (out / "logs").mkdir()
        for log in args.log:
            shutil.copy2(log, out / "logs" / Path(log).name)

    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    dirty = (
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip()
        != ""
    )
    manifest = {
        "label": args.label,
        "created_at": datetime.now(UTC).isoformat(),
        "git_commit": git.stdout.strip() or None,
        "git_dirty": dirty,
        "snapshot_path": str(out.relative_to(ROOT)),
        "table_rows": rows,
        "files": {
            str(p.relative_to(out)): {"sha256": sha256(p), "bytes": p.stat().st_size}
            for p in sorted(out.rglob("*"))
            if p.is_file()
        },
    }
    dest = ROOT / "docs" / "evidence" / f"{args.label}_manifest.json"
    dest.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"snapshot: {out.relative_to(ROOT)}  ({len(manifest['files'])} files)")
    print(f"manifest: {dest.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
