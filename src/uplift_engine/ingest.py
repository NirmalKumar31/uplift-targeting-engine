"""Download the raw X5 files and load them into the `raw` schema."""

from __future__ import annotations

import hashlib
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from uplift_engine.config import Config
from uplift_engine.warehouse import connect

# Explicit types so DuckDB doesn't guess them per file.
RAW_SCHEMAS: dict[str, dict[str, str]] = {
    "clients": {
        "client_id": "VARCHAR",
        "first_issue_date": "TIMESTAMP",
        "first_redeem_date": "TIMESTAMP",
        "age": "INTEGER",
        "gender": "VARCHAR",
    },
    "products": {
        "product_id": "VARCHAR",
        "level_1": "VARCHAR",
        "level_2": "VARCHAR",
        "level_3": "VARCHAR",
        "level_4": "VARCHAR",
        "segment_id": "DOUBLE",
        "brand_id": "VARCHAR",
        "vendor_id": "VARCHAR",
        "netto": "DOUBLE",
        "is_own_trademark": "INTEGER",
        "is_alcohol": "INTEGER",
    },
    "purchases": {
        "client_id": "VARCHAR",
        "transaction_id": "VARCHAR",
        "transaction_datetime": "TIMESTAMP",
        "regular_points_received": "DOUBLE",
        "express_points_received": "DOUBLE",
        "regular_points_spent": "DOUBLE",
        "express_points_spent": "DOUBLE",
        "purchase_sum": "DOUBLE",
        "store_id": "VARCHAR",
        "product_id": "VARCHAR",
        "product_quantity": "DOUBLE",
        "trn_sum_from_iss": "DOUBLE",
        "trn_sum_from_red": "DOUBLE",
    },
    "uplift_train": {"client_id": "VARCHAR", "treatment_flg": "INTEGER", "target": "INTEGER"},
    "uplift_test": {"client_id": "VARCHAR"},
}


class ChecksumError(ValueError):
    pass


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def download(cfg: Config, force: bool = False) -> list[Path]:
    cfg.paths.raw_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, expected in cfg.dataset.files.items():
        dest = cfg.paths.raw_dir / name
        if force or not dest.exists():
            tmp = dest.with_suffix(dest.suffix + ".part")
            urllib.request.urlretrieve(f"{cfg.dataset.base_url}/{name}", tmp)
            tmp.replace(dest)
        actual = sha256(dest)
        if actual != expected:
            raise ChecksumError(
                f"{name}: checksum mismatch (expected {expected[:12]}, got {actual[:12]})"
            )
        paths.append(dest)
    return paths


def load_raw(cfg: Config, con: duckdb.DuckDBPyConnection | None = None) -> dict[str, int]:
    """Load every raw file, refusing any whose SHA-256 differs from the config."""
    con = con or connect(cfg)
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute("CREATE SCHEMA IF NOT EXISTS meta")
    con.execute(
        "CREATE TABLE IF NOT EXISTS meta.ingest_log ("
        "table_name VARCHAR, file_name VARCHAR, sha256 VARCHAR, n_rows BIGINT, loaded_at TIMESTAMP)"
    )
    for col in ("ingest_id", "expected_sha256", "actual_sha256"):
        con.execute(f"ALTER TABLE meta.ingest_log ADD COLUMN IF NOT EXISTS {col} VARCHAR")

    files = {}
    for table in RAW_SCHEMAS:
        file = cfg.paths.raw_dir / f"{table}.csv.gz"
        if not file.exists():
            raise FileNotFoundError(f"{file} is missing; run `ute ingest` to download it")
        expected = cfg.dataset.files.get(file.name)
        actual = sha256(file)
        if expected is None or actual != expected:
            raise ChecksumError(
                f"{file.name}: SHA-256 {actual[:12]} does not match configured "
                f"{(expected or 'none')[:12]}; refusing to load"
            )
        files[table] = (file, expected, actual)

    ingest_id = f"ingest-{datetime.now(UTC):%Y%m%dT%H%M%S%f}"
    counts = {}
    for table, columns in RAW_SCHEMAS.items():
        file, expected, actual = files[table]
        cols = ", ".join(f"'{c}': '{t}'" for c, t in columns.items())
        con.execute(
            f"CREATE OR REPLACE TABLE raw.{table} AS "
            f"SELECT * FROM read_csv('{file.as_posix()}', header=true, columns={{{cols}}})"
        )
        n = con.execute(f"SELECT count(*) FROM raw.{table}").fetchone()[0]
        con.execute(
            "INSERT INTO meta.ingest_log (table_name, file_name, sha256, n_rows, loaded_at, "
            "ingest_id, expected_sha256, actual_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [table, file.name, actual, n, datetime.now(UTC), ingest_id, expected, actual],
        )
        counts[table] = n
    return counts
