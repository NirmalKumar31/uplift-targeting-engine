"""Run lineage: what code, configuration, data and features produced a result.

Every pipeline run records a row in `meta.runs` with the git commit and dirty flag (when git is
available) *and* a content fingerprint of the source tree, so lineage never degrades to "unknown".
Fingerprints are SHA-256 hex digests.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import uuid
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

import duckdb

from uplift_engine.config import Config

SOURCE_GLOBS = (
    "src/**/*.py",
    "dbt/models/**/*",
    "dbt/macros/**/*",
    "dbt/dbt_project.yml",
    "configs/**/*",
    "pyproject.toml",
    "uv.lock",
)
FEATURE_GLOBS = (
    "dbt/models/staging/**/*",
    "dbt/models/features/**/*",
    "dbt/macros/**/*",
    "dbt/dbt_project.yml",
)
PACKAGES = ("numpy", "pandas", "scikit-learn", "lightgbm", "duckdb", "dbt-duckdb", "plotly")


class LineageMismatchError(RuntimeError):
    """Artifacts, data or configuration come from different generations."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def tree_fingerprint(root: Path, globs) -> str:
    h = hashlib.sha256()
    files = sorted({p for g in globs for p in root.glob(g) if p.is_file()})
    for p in files:
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
    return h.hexdigest()


def git_state(root: Path) -> dict:
    def run(*args):
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    commit = run("rev-parse", "HEAD")
    status = run("status", "--porcelain")
    return {"git_commit": commit, "git_dirty": None if status is None else bool(status)}


def config_hash(cfg: Config) -> str:
    return sha256_bytes(json.dumps(cfg.fingerprint_payload(), sort_keys=True).encode())


def environment() -> dict:
    versions = {}
    for pkg in PACKAGES:
        try:
            versions[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            versions[pkg] = None
    return {"python": platform.python_version(), "packages": versions}


def environment_fingerprint(root: Path) -> str:
    lock = root / "uv.lock"
    payload = {"env": environment(), "uv_lock": sha256_file(lock) if lock.exists() else None}
    return sha256_bytes(json.dumps(payload, sort_keys=True).encode())


def feature_code_fingerprint(root: Path, as_of) -> str:
    return sha256_bytes((tree_fingerprint(root, FEATURE_GLOBS) + str(as_of)).encode())


def dataset_fingerprint(con: duckdb.DuckDBPyConnection) -> str:
    """Digest of the verified raw-file hashes of the most recent ingest."""
    rows = con.execute(
        "SELECT table_name, actual_sha256 FROM meta.ingest_log "
        "WHERE ingest_id = (SELECT ingest_id FROM meta.ingest_log ORDER BY loaded_at DESC LIMIT 1) "
        "ORDER BY table_name"
    ).fetchall()
    if not rows:
        raise LineageMismatchError("no verified ingest recorded; run `ute ingest`")
    return sha256_bytes(json.dumps(rows).encode())


def feature_schema_fingerprint(con: duckdb.DuckDBPyConnection) -> str:
    cols = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'features' AND table_name = 'client_features' "
        "ORDER BY ordinal_position"
    ).fetchall()
    if not cols:
        raise LineageMismatchError("features.client_features is missing; run `ute transform`")
    return sha256_bytes(json.dumps(cols).encode())


def current_feature_build(con: duckdb.DuckDBPyConnection) -> dict:
    row = con.execute(
        "SELECT build_id, dataset_fp, feature_code_fp, schema_fp, as_of FROM meta.feature_build "
        "ORDER BY built_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise LineageMismatchError("no feature build recorded; run `ute transform`")
    return dict(
        zip(["build_id", "dataset_fp", "feature_code_fp", "schema_fp", "as_of"], row, strict=True)
    )


def ensure_meta(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS meta")
    con.execute(
        "CREATE TABLE IF NOT EXISTS meta.runs (run_id VARCHAR PRIMARY KEY, stage VARCHAR, "
        "release_id VARCHAR, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, status VARCHAR, "
        "git_commit VARCHAR, git_dirty BOOLEAN, source_fp VARCHAR, config_hash VARCHAR, "
        "dataset_fp VARCHAR, feature_build_id VARCHAR, env_fp VARCHAR, seed INTEGER, "
        "parent_run_ids JSON, details JSON)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS meta.test_access_log (accessed_at TIMESTAMP, champion VARCHAR, "
        "git_sha VARCHAR, reason VARCHAR, bundle_sha256 VARCHAR)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS meta.feature_build (build_id VARCHAR, built_at TIMESTAMPTZ, "
        "dataset_fp VARCHAR, feature_code_fp VARCHAR, schema_fp VARCHAR, as_of TIMESTAMP)"
    )


class Run:
    """Records one pipeline stage in meta.runs (status running -> succeeded/failed)."""

    def __init__(
        self,
        con,
        cfg: Config,
        stage: str,
        release_id: str | None = None,
        parents: list[str] | None = None,
        needs_features: bool = True,
    ):
        self.con, self.cfg, self.stage = con, cfg, stage
        self.release_id, self.parents = release_id, parents or []
        self.run_id = f"{stage}-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
        self.details: dict = {}
        self.needs_features = needs_features

    def __enter__(self) -> Run:
        ensure_meta(self.con)
        root = self.cfg.root
        build = current_feature_build(self.con) if self.needs_features else {}
        self.lineage = {
            **git_state(root),
            "source_fp": tree_fingerprint(root, SOURCE_GLOBS),
            "config_hash": config_hash(self.cfg),
            "dataset_fp": build.get("dataset_fp"),
            "feature_build_id": build.get("build_id"),
            "env_fp": environment_fingerprint(root),
        }
        self.con.execute(
            "INSERT INTO meta.runs VALUES (?, ?, ?, ?, NULL, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "NULL)",
            [
                self.run_id,
                self.stage,
                self.release_id,
                datetime.now(UTC),
                self.lineage["git_commit"],
                self.lineage["git_dirty"],
                self.lineage["source_fp"],
                self.lineage["config_hash"],
                self.lineage["dataset_fp"],
                self.lineage["feature_build_id"],
                self.lineage["env_fp"],
                self.cfg.experiment.seed,
                json.dumps(self.parents),
            ],
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        status = "failed" if exc_type else "succeeded"
        if exc_type:
            self.details["error"] = f"{exc_type.__name__}: {exc}"
        self.con.execute(
            "UPDATE meta.runs SET finished_at = ?, status = ?, details = ? WHERE run_id = ?",
            [datetime.now(UTC), status, json.dumps(self.details, default=str), self.run_id],
        )

    @property
    def record(self) -> dict:
        return {
            "run_id": self.run_id,
            "stage": self.stage,
            "release_id": self.release_id,
            **self.lineage,
            "seed": self.cfg.experiment.seed,
            "parents": self.parents,
            "environment": environment(),
        }
