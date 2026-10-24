"""DuckDB warehouse access: one file, schemas raw -> staging/features (dbt) -> ml -> marts (dbt)."""

from __future__ import annotations

import duckdb
import pandas as pd

from uplift_engine.config import Config


def connect(cfg: Config, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    cfg.paths.warehouse.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(cfg.paths.warehouse), read_only=read_only)


def write_table(con: duckdb.DuckDBPyConnection, df: pd.DataFrame, table: str) -> None:
    """Replace `schema.table` with the contents of a DataFrame."""
    schema = table.split(".")[0]
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    con.register("_df", df)
    con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM _df")
    con.unregister("_df")


def read_table(con: duckdb.DuckDBPyConnection, query_or_table: str) -> pd.DataFrame:
    sql = query_or_table if " " in query_or_table.strip() else f"SELECT * FROM {query_or_table}"
    return con.execute(sql).df()
