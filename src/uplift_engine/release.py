"""Versioned releases: validation, a single commit point, and recovery.

A release directory is everything consumers read, produced by one consistent generation of runs:

    releases/<release_id>/
        bundle.joblib                 model + calibrator + contract + economics (+ deploy run id)
        scores.parquet                every customer's score and eligibility (+ producer_run_id)
        campaign_assignment.parquet   frozen campaign (+ producer_run_id)
        assessment_predictions.parquet  outer-fold predictions (+ producer_run_id)
        marts/                        BI marts + results.json + PRODUCERS.json + RELEASE.json
        results.json                  compact results artifact
        manifest.json                 runs, lineage, file hashes (written last, before commit)

Commit point. `releases/ACTIVE` is replaced with one atomic `os.replace`. Before it, nothing any
consumer reads has changed; after it, the API and the dashboard both resolve the new release
(each re-reads the pointer and reads only that immutable directory). Everything after the commit
is *derived* state, re-derivable from the pointer at any time by `reconcile`:

    * the committed dashboard export `data/marts/` (a copy for fresh clones and Docker images; it
      is not an authority when a pointer exists),
    * `meta.releases` statuses.

A crash or I/O error before the commit leaves the previous release fully active; after it, the new
release is fully active and `reconcile` (run automatically by `ute release`/`ute status`/`ute run`)
brings the export and statuses in line. `releases/JOURNAL.json` records the phase of the last
activation for diagnosis.
"""

from __future__ import annotations

import json
import os
import shutil
import warnings
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import joblib
import pandas as pd

from uplift_engine.config import Config
from uplift_engine.lineage import LINEAGE_FIELDS, LineageMismatchError, sha256_file

REQUIRED_STAGES = ("assess", "deploy", "explain", "marts")
OPTIONAL_STAGES = ("historical",)
REQUIRED_FILES = (
    "bundle.joblib",
    "scores.parquet",
    "campaign_assignment.parquet",
    "assessment_predictions.parquet",
    "results.json",
    "marts/results.json",
    "marts/PRODUCERS.json",
)


class ReleaseError(RuntimeError):
    pass


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS meta")
    con.execute(
        "CREATE TABLE IF NOT EXISTS meta.releases (release_id VARCHAR PRIMARY KEY, "
        "created_at TIMESTAMPTZ, status VARCHAR, activated_at TIMESTAMPTZ, manifest JSON)"
    )


def release_dir(cfg: Config, release_id: str) -> Path:
    return cfg.paths.releases_dir / release_id


def new_release(cfg: Config, con: duckdb.DuckDBPyConnection) -> str:
    ensure_table(con)
    rid = f"rel-{datetime.now(UTC):%Y%m%dT%H%M%S%f}"
    release_dir(cfg, rid).mkdir(parents=True, exist_ok=False)
    con.execute(
        "INSERT INTO meta.releases VALUES (?, ?, 'building', NULL, NULL)", [rid, datetime.now(UTC)]
    )
    return rid


def building_release(cfg: Config, con: duckdb.DuckDBPyConnection, create: bool = True) -> str:
    ensure_table(con)
    row = con.execute(
        "SELECT release_id FROM meta.releases WHERE status = 'building' "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row:
        return row[0]
    if not create:
        raise ReleaseError(
            "no release is being built; start one with `ute run` or `ute assess --new-release`"
        )
    return new_release(cfg, con)


def latest_runs(con, release_id: str) -> dict[str, dict]:
    """Latest run of each stage in a release (any status), with full lineage."""
    df = con.execute(
        "SELECT * FROM meta.runs WHERE release_id = ? ORDER BY started_at", [release_id]
    ).df()
    out = {}
    for row in df.to_dict("records"):
        row["parents"] = json.loads(row["parent_run_ids"] or "[]")
        out[row["stage"]] = row
    return out


def stage_runs(con, release_id: str) -> dict[str, dict]:  # backwards-compatible name
    return latest_runs(con, release_id)


def expected_marts_parents(runs: dict[str, dict]) -> list[str]:
    stages = [s for s in ("assess", "deploy", "explain", "historical") if s in runs]
    return sorted(runs[s]["run_id"] for s in stages)


def validate(cfg: Config, con, release_id: str) -> dict:
    """Check status, lineage completeness and equality, parentage, and that every file was
    produced by exactly the runs the release names. Raises ReleaseError listing every problem."""
    runs = latest_runs(con, release_id)
    problems = []
    for stage in REQUIRED_STAGES:
        r = runs.get(stage)
        if r is None:
            problems.append(f"stage {stage} has no run in this release")
        elif r["status"] != "succeeded":
            problems.append(f"stage {stage} run {r['run_id']} is {r['status']}")
    for stage in OPTIONAL_STAGES:
        r = runs.get(stage)
        if r is not None and r["status"] != "succeeded":
            problems.append(f"optional stage {stage} was attempted and is {r['status']}")
    considered = {s: r for s, r in runs.items() if s in REQUIRED_STAGES + OPTIONAL_STAGES}

    for field in LINEAGE_FIELDS:
        values = {
            s: (None if pd.isna(r.get(field)) else r.get(field)) for s, r in considered.items()
        }
        missing = sorted(s for s, v in values.items() if not v)
        if missing:
            problems.append(f"missing {field} in runs: {missing}")
        distinct = {v for v in values.values() if v}
        if len(distinct) > 1:
            problems.append(
                f"runs come from different generations ({field} differs: "
                + ", ".join(f"{s}={str(v)[:10]}" for s, v in sorted(values.items()))
                + ")"
            )

    if (
        "explain" in runs
        and "deploy" in runs
        and runs["explain"]["parents"] != [runs["deploy"]["run_id"]]
    ):
        problems.append(
            f"explain run {runs['explain']['run_id']} was built from "
            f"{runs['explain']['parents']}, not the latest deploy "
            f"{runs['deploy']['run_id']}: rerun `ute explain` and `ute marts`"
        )
    if "marts" in runs:
        want = expected_marts_parents(runs)
        if sorted(runs["marts"]["parents"]) != want:
            problems.append(
                f"marts were built from {sorted(runs['marts']['parents'])}, but the "
                f"latest upstream runs are {want}: rerun `ute marts`"
            )
        for s in ("assess", "deploy", "explain", "historical"):
            r = runs.get(s)
            if (
                r is not None
                and r["finished_at"] is not None
                and r["finished_at"] > runs["marts"]["started_at"]
            ):
                problems.append(f"{s} finished after marts started: rerun `ute marts`")

    rdir = release_dir(cfg, release_id)
    for f in REQUIRED_FILES:
        if not (rdir / f).exists():
            problems.append(f"missing {f}")
    if not problems:
        problems += _check_producers(rdir, runs)
    if problems:
        raise ReleaseError(
            "release "
            + release_id
            + " is incomplete or inconsistent:\n  - "
            + "\n  - ".join(problems)
        )
    files = {
        p.relative_to(rdir).as_posix(): sha256_file(p)
        for p in sorted(rdir.rglob("*"))
        if p.is_file() and p.name not in ("manifest.json", "RELEASE.json")
    }
    lineage = {f: runs["deploy"][f] for f in LINEAGE_FIELDS}
    return {
        "release_id": release_id,
        "validated_at": datetime.now(UTC).isoformat(),
        "runs": {s: r["run_id"] for s, r in considered.items()},
        "historical_available": "historical" in considered,
        **lineage,
        "policy_version": sha256_file(rdir / "bundle.joblib")[:16],
        "files": files,
    }


def _check_producers(rdir: Path, runs: dict[str, dict]) -> list[str]:
    problems = []
    deploy, assess = runs["deploy"]["run_id"], runs["assess"]["run_id"]
    policy = sha256_file(rdir / "bundle.joblib")[:16]
    bundle = joblib.load(rdir / "bundle.joblib")
    if bundle.get("deploy_run_id") != deploy:
        problems.append(
            f"bundle.joblib was produced by {bundle.get('deploy_run_id')}, not {deploy}"
        )
    for name, producer in (
        ("scores.parquet", deploy),
        ("campaign_assignment.parquet", deploy),
        ("assessment_predictions.parquet", assess),
    ):
        found = set(pd.read_parquet(rdir / name, columns=["producer_run_id"]).producer_run_id)
        if found != {producer}:
            problems.append(f"{name} was produced by {sorted(found)}, not {producer}")
    camp = set(
        pd.read_parquet(
            rdir / "campaign_assignment.parquet", columns=["policy_version"]
        ).policy_version
    )
    if camp != {policy}:
        problems.append(f"campaign assignment is for policy {sorted(camp)}, bundle is {policy}")
    want = {
        s: runs[s]["run_id"] for s in ("assess", "deploy", "explain", "historical") if s in runs
    }
    producers = json.loads((rdir / "marts" / "PRODUCERS.json").read_text())
    if producers.get("runs") != want:
        problems.append(f"marts/PRODUCERS.json names {producers.get('runs')}, expected {want}")
    for path in ("results.json", "marts/results.json"):
        res = json.loads((rdir / path).read_text())
        if res.get("runs", {}) != want or res.get("policy_version") != policy:
            problems.append(f"{path} was generated from other runs or another policy")
    dim = rdir / "marts" / "dim_client.parquet"
    if dim.exists():
        pv = set(pd.read_parquet(dim, columns=["policy_version"]).policy_version)
        if pv != {policy}:
            problems.append(f"marts/dim_client.parquet holds policy {sorted(pv)}, not {policy}")
    return problems


# --------------------------------------------------------------------------- activation
def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with tmp.open("w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _journal(cfg: Config, release_id: str, phase: str, **extra) -> None:
    cfg.paths.releases_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        cfg.paths.releases_dir / "JOURNAL.json",
        json.dumps(
            {"release_id": release_id, "phase": phase, "at": datetime.now(UTC).isoformat(), **extra}
        ),
    )


def pointer_path(cfg: Config) -> Path:
    return cfg.paths.releases_dir / "ACTIVE"


def activate(cfg: Config, con, release_id: str) -> dict:
    """Validate, finalize the immutable release directory, then commit with one os.replace."""
    reconcile(cfg, con)
    try:
        manifest = validate(cfg, con, release_id)
    except ReleaseError:
        con.execute("UPDATE meta.releases SET status = 'failed' WHERE release_id = ?", [release_id])
        raise
    rdir = release_dir(cfg, release_id)
    _journal(cfg, release_id, "validated")
    _atomic_write(
        rdir / "marts" / "RELEASE.json",
        json.dumps(
            {
                "release_id": release_id,
                "policy_version": manifest["policy_version"],
                "files": {k: v for k, v in manifest["files"].items() if k.startswith("marts/")},
            },
            indent=2,
        ),
    )
    _atomic_write(rdir / "manifest.json", json.dumps(manifest, indent=2))

    # ---- commit point: the only step that changes what consumers read -------------------------
    _atomic_write(
        pointer_path(cfg),
        json.dumps({"release_id": release_id, "activated_at": datetime.now(UTC).isoformat()}),
    )
    _journal(cfg, release_id, "committed")
    # ---- derived state (re-derivable from the pointer) ----------------------------------------
    try:
        reconcile(cfg, con)
    except Exception as exc:  # the release IS active; only derived copies lag behind
        _journal(cfg, release_id, "committed-reconcile-pending", error=repr(exc))
        warnings.warn(
            f"release {release_id} is active, but deriving the export/statuses "
            f"failed ({exc!r}); `ute status` repairs it",
            stacklevel=2,
        )
    return manifest


def active_release(cfg: Config) -> tuple[str, Path]:
    pointer = pointer_path(cfg)
    if not pointer.exists():
        raise ReleaseError("no active release; run `ute run` (or `ute release`) first")
    rid = json.loads(pointer.read_text())["release_id"]
    rdir = release_dir(cfg, rid)
    if not (rdir / "manifest.json").exists():
        raise ReleaseError(f"active release {rid} has no manifest")
    return rid, rdir


def sync_export(cfg: Config, release_id: str) -> None:
    """Make data/marts a copy of the release's marts (idempotent; staged then swapped)."""
    rdir = release_dir(cfg, release_id)
    marts = cfg.paths.marts_dir
    staged, prev = marts.with_name("marts.next"), marts.with_name("marts.prev")
    shutil.rmtree(staged, ignore_errors=True)
    shutil.copytree(rdir / "marts", staged)
    shutil.rmtree(prev, ignore_errors=True)
    if marts.exists():
        marts.rename(prev)
    staged.rename(marts)
    shutil.rmtree(prev, ignore_errors=True)


def export_release_id(cfg: Config) -> str | None:
    path = cfg.paths.marts_dir / "RELEASE.json"
    try:
        return json.loads(path.read_text())["release_id"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return None


def reconcile(cfg: Config, con=None) -> str | None:
    """Re-derive everything that follows the commit point from `releases/ACTIVE`."""
    marts, prev = cfg.paths.marts_dir, cfg.paths.marts_dir.with_name("marts.prev")
    if not marts.exists() and prev.exists():  # interrupted export swap: restore first
        prev.rename(marts)
    try:
        rid, _ = active_release(cfg)
    except ReleaseError:
        return None
    if export_release_id(cfg) != rid:
        sync_export(cfg, rid)
    if con is not None:
        ensure_table(con)
        manifest = (release_dir(cfg, rid) / "manifest.json").read_text()
        con.execute(
            "UPDATE meta.releases SET status = 'superseded' "
            "WHERE status = 'active' AND release_id <> ?",
            [rid],
        )
        con.execute(
            "UPDATE meta.releases SET status = 'active', manifest = ?, "
            "activated_at = coalesce(activated_at, ?) WHERE release_id = ?",
            [manifest, datetime.now(UTC), rid],
        )
    _journal(cfg, rid, "reconciled")
    return rid


def restore_marts(cfg: Config) -> None:  # kept for callers of the old name
    reconcile(cfg)


def verify_file(rdir: Path, name: str) -> Path:
    """Check a release file against its manifest hash before use."""
    manifest = json.loads((rdir / "manifest.json").read_text())
    path = rdir / name
    if not path.exists():
        raise ReleaseError(f"{path} is missing")
    if manifest["files"].get(name) != sha256_file(path):
        raise LineageMismatchError(f"{path} does not match the release manifest")
    return path
