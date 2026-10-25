"""DuckDB warehouse access: raw -> staging/features (dbt) -> ml history -> marts (dbt).

Model outputs are *history* tables: every row carries the `run_id` that produced it, and a rerun
appends instead of replacing, so earlier evaluations stay queryable. Marts select the runs of one
release. The warehouse is the build workspace; the release on disk is what consumers read.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from uplift_engine.config import Config


def connect(cfg: Config, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    cfg.paths.warehouse.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(cfg.paths.warehouse), read_only=read_only)


def write_table(con: duckdb.DuckDBPyConnection, df: pd.DataFrame, table: str) -> None:
    """Replace `schema.table` (for derived, non-historical tables only)."""
    schema = table.split(".")[0]
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    con.register("_df", df)
    con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM _df")
    con.unregister("_df")


def append_run(con: duckdb.DuckDBPyConnection, df: pd.DataFrame, table: str, run_id: str) -> None:
    """Append rows tagged with run_id; new columns are added, rows of the same run replaced."""
    schema = table.split(".")[0]
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    df = df.assign(run_id=run_id)
    con.register("_df", df)
    try:
        exists = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? "
            "AND table_name = ?",
            table.split("."),
        ).fetchone()[0]
        if not exists:
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM _df")
            return
        have = {r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()}
        types = {r[0]: r[1] for r in con.execute("DESCRIBE _df").fetchall()}
        for col in df.columns:
            if col not in have:
                con.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" {types[col]}')
        con.execute(f"DELETE FROM {table} WHERE run_id = ?", [run_id])
        con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _df")
    finally:
        con.unregister("_df")


def read_table(con: duckdb.DuckDBPyConnection, query_or_table: str) -> pd.DataFrame:
    sql = query_or_table if " " in query_or_table.strip() else f"SELECT * FROM {query_or_table}"
    return con.execute(sql).df()
