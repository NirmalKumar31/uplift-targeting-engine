"""Run lineage: code, environment, config, data and feature-build fingerprints for every run."""

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

COMPUTE_GLOBS = (
    "src/uplift_engine/**/*.py",
    "dbt/models/**/*",
    "dbt/macros/**/*",
    "dbt/dbt_project.yml",
    "configs/**/*",
    "uv.lock",
)
FEATURE_GLOBS = (
    "dbt/models/sources.yml",
    "dbt/models/staging/**/*",
    "dbt/models/features/**/*",
    "dbt/macros/**/*",
    "dbt/dbt_project.yml",
)
PACKAGES = (
    "numpy",
    "pandas",
    "scikit-learn",
    "scipy",
    "lightgbm",
    "duckdb",
    "dbt-duckdb",
    "pyarrow",
)
LINEAGE_FIELDS = ("source_fp", "env_fp", "config_hash", "dataset_fp", "feature_build_id")


class LineageMismatchError(RuntimeError):
    """Inputs or artifacts come from different generations."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def tree_manifest(root: Path, globs) -> dict[str, str]:
    files = sorted({p for g in globs for p in root.glob(g) if p.is_file()})
    return {p.relative_to(root).as_posix(): sha256_file(p) for p in files}


def _digest(payload) -> str:
    return sha256_bytes(json.dumps(payload, sort_keys=True).encode())


def tree_fingerprint(root: Path, globs) -> str:
    return _digest(tree_manifest(root, globs))


def git_state(root: Path) -> dict:
    def run(*args):
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    commit = run("rev-parse", "HEAD")
    status = run("status", "--porcelain")
    return {"git_commit": commit, "git_dirty": None if status is None else bool(status)}


def config_hash(cfg: Config) -> str:
    return _digest(cfg.fingerprint_payload())


def environment(root: Path | None = None) -> dict:
    versions = {}
    for pkg in PACKAGES:
        try:
            versions[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            versions[pkg] = None
    lock = (root / "uv.lock") if root else None
    return {
        "python": platform.python_version(),
        "packages": versions,
        "uv_lock": sha256_file(lock) if lock and lock.exists() else None,
    }


def feature_code_fingerprint(root: Path, as_of) -> str:
    return _digest({"files": tree_manifest(root, FEATURE_GLOBS), "as_of": str(as_of)})


def dataset_fingerprint(con: duckdb.DuckDBPyConnection) -> str:
    try:
        rows = con.execute(
            "SELECT table_name, actual_sha256 FROM meta.ingest_log WHERE ingest_id = "
            "(SELECT ingest_id FROM meta.ingest_log WHERE ingest_id IS NOT NULL "
            "ORDER BY loaded_at DESC LIMIT 1) ORDER BY table_name"
        ).fetchall()
    except duckdb.CatalogException:
        rows = []
    if not rows:
        raise LineageMismatchError("no verified ingest recorded; run `ute ingest`")
    return _digest(rows)


def feature_schema_fingerprint(con: duckdb.DuckDBPyConnection) -> str:
    cols = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'features' AND table_name = 'client_features' "
        "ORDER BY ordinal_position"
    ).fetchall()
    if not cols:
        raise LineageMismatchError("features.client_features is missing; run `ute transform`")
    return _digest(cols)


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
    con.execute("ALTER TABLE meta.feature_build ADD COLUMN IF NOT EXISTS status VARCHAR")
    con.execute("ALTER TABLE meta.feature_build ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ")
    con.execute(
        "CREATE TABLE IF NOT EXISTS meta.fingerprint_payloads (fingerprint VARCHAR PRIMARY KEY, "
        "kind VARCHAR, payload JSON, recorded_at TIMESTAMPTZ)"
    )


def _store_payload(con, kind: str, payload) -> str:
    fp = _digest(payload)
    con.execute(
        "INSERT OR IGNORE INTO meta.fingerprint_payloads VALUES (?, ?, ?, ?)",
        [fp, kind, json.dumps(payload, sort_keys=True), datetime.now(UTC)],
    )
    return fp


def code_fingerprint(con, root: Path) -> str:
    return _store_payload(con, "code", tree_manifest(root, COMPUTE_GLOBS))


def environment_fingerprint(con, root: Path) -> str:
    return _store_payload(con, "environment", environment(root))


def begin_feature_build(con, cfg: Config) -> str:
    """Mark a feature build as in progress before dbt touches the workspace."""
    ensure_meta(con)
    build_id = f"features-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    con.execute(
        "INSERT INTO meta.feature_build (build_id, built_at, dataset_fp, feature_code_fp, "
        "schema_fp, as_of, status) VALUES (?, ?, ?, ?, NULL, ?, 'building')",
        [
            build_id,
            datetime.now(UTC),
            dataset_fingerprint(con),
            feature_code_fingerprint(cfg.root, cfg.dataset.as_of),
            cfg.dataset.as_of,
        ],
    )
    return build_id


def finish_feature_build(con, build_id: str, ok: bool) -> None:
    schema = feature_schema_fingerprint(con) if ok else None
    con.execute(
        "UPDATE meta.feature_build SET status = ?, schema_fp = ?, finished_at = ? "
        "WHERE build_id = ?",
        ["succeeded" if ok else "failed", schema, datetime.now(UTC), build_id],
    )


def require_fresh_features(con, cfg: Config) -> dict:
    """Latest feature build, only if it succeeded and still matches config, SQL, data and schema."""
    ensure_meta(con)
    row = con.execute(
        "SELECT build_id, dataset_fp, feature_code_fp, schema_fp, as_of, status "
        "FROM meta.feature_build ORDER BY built_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise LineageMismatchError("no feature build recorded; run `ute transform`")
    build = dict(
        zip(
            ["build_id", "dataset_fp", "feature_code_fp", "schema_fp", "as_of", "status"],
            row,
            strict=True,
        )
    )
    problems = []
    if build["status"] != "succeeded":
        problems.append(
            f"latest feature build {build['build_id']} is "
            f"{build['status'] or 'of an unverified legacy format'}"
        )
    if build["as_of"] != cfg.dataset.as_of.replace(tzinfo=None):
        problems.append(f"built for as_of {build['as_of']}, config says {cfg.dataset.as_of}")
    if build["feature_code_fp"] != feature_code_fingerprint(cfg.root, cfg.dataset.as_of):
        problems.append("feature SQL/macros changed since the build")
    if build["dataset_fp"] != dataset_fingerprint(con):
        problems.append("raw data were re-ingested since the build")
    if build["schema_fp"] != feature_schema_fingerprint(con):
        problems.append("features.client_features no longer matches the build's schema")
    if problems:
        raise LineageMismatchError(
            "stale or incomplete feature build: " + "; ".join(problems) + ". Run `ute transform`."
        )
    return build


class Run:
    """Context manager that records one pipeline stage in meta.runs."""

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
        self.release_id, self.parents = release_id, list(parents or [])
        self.run_id = f"{stage}-{datetime.now(UTC):%Y%m%dT%H%M%S%f}-{uuid.uuid4().hex[:6]}"
        self.details: dict = {}
        self.needs_features = needs_features

    def __enter__(self) -> Run:
        ensure_meta(self.con)
        root = self.cfg.root
        build = require_fresh_features(self.con, self.cfg) if self.needs_features else {}
        self.lineage = {
            **git_state(root),
            "source_fp": code_fingerprint(self.con, root),
            "config_hash": config_hash(self.cfg),
            "dataset_fp": build.get("dataset_fp"),
            "feature_build_id": build.get("build_id"),
            "env_fp": environment_fingerprint(self.con, root),
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
            "UPDATE meta.runs SET finished_at = ?, status = ?, details = ?, parent_run_ids = ? "
            "WHERE run_id = ?",
            [
                datetime.now(UTC),
                status,
                json.dumps(self.details, default=str),
                json.dumps(self.parents),
                self.run_id,
            ],
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
            "environment": environment(self.cfg.root),
        }
