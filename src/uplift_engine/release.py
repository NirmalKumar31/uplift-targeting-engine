"""Versioned releases and atomic activation.

A release is everything consumers read, produced by one consistent set of runs:

    releases/<release_id>/
        bundle.joblib                 model + calibrator + contract + economics + action semantics
        scores.parquet                every customer's score, effect, eligibility
        campaign_assignment.parquet   frozen campaign: action + holdout assignment per customer
        marts/*.parquet               BI marts for this release
        results.json                  compact results artifact (dashboards/README trace to it)
        manifest.json                 runs, fingerprints, file hashes (written at validation)

Lifecycle: `building` -> `active` (or `failed`). Activation happens only after validation passes:
required stages succeeded, all runs share one config hash, dataset fingerprint and feature build,
and every required file exists. Then `releases/ACTIVE` is replaced atomically (write temp file +
os.replace) and the committed dashboard export `data/marts/` is swapped (rename old -> new). If
anything fails before the pointer is replaced, the previous release stays active. The two-rename
mart swap is not a single atomic operation; `restore_marts` repairs an interrupted swap.
Consumers (API, dashboard) key their caches on the release id, so activation is picked up without
manual cache clearing (the API re-reads the pointer per request; see api.py).
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from uplift_engine.config import Config
from uplift_engine.lineage import LineageMismatchError, sha256_file

REQUIRED_STAGES = ("assess", "deploy", "historical", "explain", "marts")
REQUIRED_FILES = ("bundle.joblib", "scores.parquet", "campaign_assignment.parquet", "results.json")


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
    rid = f"rel-{datetime.now(UTC):%Y%m%dT%H%M%S}"
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
        raise ReleaseError("no release is being built; start one with `ute assess` or `ute run`")
    return new_release(cfg, con)


def stage_runs(con, release_id: str) -> dict[str, dict]:
    rows = con.execute(
        "SELECT stage, run_id, status, config_hash, dataset_fp, feature_build_id FROM meta.runs "
        "WHERE release_id = ? ORDER BY started_at",
        [release_id],
    ).fetchall()
    latest = {}
    for stage, run_id, status, ch, dfp, fb in rows:
        latest[stage] = {
            "run_id": run_id,
            "status": status,
            "config_hash": ch,
            "dataset_fp": dfp,
            "feature_build_id": fb,
        }
    return latest


def validate(cfg: Config, con, release_id: str) -> dict:
    runs = stage_runs(con, release_id)
    problems = []
    for stage in REQUIRED_STAGES:
        r = runs.get(stage)
        if r is None:
            problems.append(f"stage {stage} has no run in this release")
        elif r["status"] != "succeeded":
            problems.append(f"stage {stage} run {r['run_id']} is {r['status']}")
    for key in ("config_hash", "dataset_fp", "feature_build_id"):
        values = {r[key] for s, r in runs.items() if s in REQUIRED_STAGES and r.get(key)}
        if len(values) > 1:
            problems.append(f"runs disagree on {key}: {sorted(values)}")
    rdir = release_dir(cfg, release_id)
    for f in REQUIRED_FILES:
        if not (rdir / f).exists():
            problems.append(f"missing {f}")
    marts = sorted((rdir / "marts").glob("*.parquet")) if (rdir / "marts").exists() else []
    if not marts:
        problems.append("no exported marts")
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
        if p.is_file() and p.name != "manifest.json"
    }
    first = runs["deploy"]
    return {
        "release_id": release_id,
        "validated_at": datetime.now(UTC).isoformat(),
        "runs": {s: runs[s]["run_id"] for s in REQUIRED_STAGES},
        "config_hash": first["config_hash"],
        "dataset_fp": first["dataset_fp"],
        "feature_build_id": first["feature_build_id"],
        "files": files,
    }


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def restore_marts(cfg: Config) -> None:
    """Repair an interrupted mart swap: if data/marts is missing, put the previous copy back."""
    marts, prev = cfg.paths.marts_dir, cfg.paths.marts_dir.with_name("marts.prev")
    if not marts.exists() and prev.exists():
        prev.rename(marts)


def activate(cfg: Config, con, release_id: str) -> dict:
    """Validate, then atomically point consumers at this release. Raises without side effects
    on the active release when validation fails."""
    restore_marts(cfg)
    try:
        manifest = validate(cfg, con, release_id)
    except ReleaseError:
        con.execute("UPDATE meta.releases SET status = 'failed' WHERE release_id = ?", [release_id])
        raise
    rdir = release_dir(cfg, release_id)
    _atomic_write(rdir / "manifest.json", json.dumps(manifest, indent=2))

    # Stage the dashboard export next to the live one, then swap.
    marts, staged = cfg.paths.marts_dir, cfg.paths.marts_dir.with_name("marts.next")
    prev = cfg.paths.marts_dir.with_name("marts.prev")
    shutil.rmtree(staged, ignore_errors=True)
    shutil.copytree(rdir / "marts", staged)
    shutil.copy2(rdir / "results.json", staged / "results.json")
    (staged / "RELEASE.json").write_text(
        json.dumps(
            {
                "release_id": release_id,
                "files": {
                    k: v
                    for k, v in manifest["files"].items()
                    if k.startswith("marts/") or k == "results.json"
                },
            },
            indent=2,
        )
    )

    pointer = cfg.paths.releases_dir / "ACTIVE"
    _atomic_write(
        pointer,
        json.dumps({"release_id": release_id, "activated_at": datetime.now(UTC).isoformat()}),
    )
    shutil.rmtree(prev, ignore_errors=True)
    if marts.exists():
        marts.rename(prev)
    staged.rename(marts)
    shutil.rmtree(prev, ignore_errors=True)

    con.execute("UPDATE meta.releases SET status = 'superseded' WHERE status = 'active'")
    con.execute(
        "UPDATE meta.releases SET status = 'active', activated_at = ?, manifest = ? "
        "WHERE release_id = ?",
        [datetime.now(UTC), json.dumps(manifest), release_id],
    )
    return manifest


def active_release(cfg: Config) -> tuple[str, Path]:
    pointer = cfg.paths.releases_dir / "ACTIVE"
    if not pointer.exists():
        raise ReleaseError("no active release; run `ute run` (or `ute release`) first")
    rid = json.loads(pointer.read_text())["release_id"]
    rdir = release_dir(cfg, rid)
    manifest = rdir / "manifest.json"
    if not manifest.exists():
        raise ReleaseError(f"active release {rid} has no manifest")
    return rid, rdir


def verify_file(rdir: Path, name: str) -> Path:
    """Check a release file against its manifest hash before use."""
    manifest = json.loads((rdir / "manifest.json").read_text())
    path = rdir / name
    if not path.exists():
        raise ReleaseError(f"{path} is missing")
    if manifest["files"].get(name) != sha256_file(path):
        raise LineageMismatchError(f"{path} does not match the release manifest")
    return path
