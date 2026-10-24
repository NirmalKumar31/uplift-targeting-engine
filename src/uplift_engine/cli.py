"""`ute` command line: every pipeline stage, plus `ute run` for the whole thing end to end."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from uplift_engine import ingest as ingest_mod
from uplift_engine import pipeline
from uplift_engine.config import REPO_ROOT, Config, load_config
from uplift_engine.warehouse import connect

app = typer.Typer(add_completion=False, help="Uplift targeting engine pipeline.")
console = Console()
ConfigOpt = typer.Option(
    None, "--config", "-c", help="Pipeline YAML (default configs/pipeline.yaml)"
)


def _cfg(path: Path | None) -> Config:
    return load_config(path)


def _dbt(cfg: Config, *args: str) -> None:
    exe = Path(sys.executable).parent / "dbt"
    env = {**os.environ, "UTE_WAREHOUSE": str(cfg.paths.warehouse)}
    project = REPO_ROOT / "dbt"
    cmd = [str(exe), *args, "--project-dir", str(project), "--profiles-dir", str(project)]
    subprocess.run(cmd, check=True, env=env, cwd=project)


def _log(msg: str) -> None:
    console.print(msg)


@app.command()
def ingest(config: Path | None = ConfigOpt, force: bool = False) -> None:
    """Download the raw files (SHA-256 verified) and land them in the warehouse."""
    cfg = _cfg(config)
    ingest_mod.download(cfg, force=force)
    with connect(cfg) as con:
        counts = ingest_mod.load_raw(cfg, con)
    for table, n in counts.items():
        console.print(f"  raw.{table:14s} {n:>12,} rows")


@app.command()
def transform(config: Path | None = ConfigOpt) -> None:
    """dbt: staging models + client feature store, with data tests."""
    _dbt(_cfg(config), "build", "--select", "tag:features")


@app.command()
def train(config: Path | None = ConfigOpt) -> None:
    """Cross-fit every candidate learner on the development set and pick the champion."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        board = pipeline.train(cfg, con, log=_log)
    table = Table(title="Out-of-fold leaderboard (development set)")
    for col in ("rank", "model", "qini", "95% CI", "uplift@10%", "uplift@30%"):
        table.add_column(col)
    for r in board.itertuples():
        table.add_row(
            str(r.rank),
            r.model,
            f"{r.qini_auc:+.4f}",
            f"[{r.qini_auc_low:+.4f}, {r.qini_auc_high:+.4f}]",
            f"{r.uplift_at_10:+.2%}",
            f"{r.uplift_at_30:+.2%}",
        )
    console.print(table)


@app.command()
def evaluate(config: Path | None = ConfigOpt) -> None:
    """Score the locked test split once with the dev-fitted champion and references."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        res = pipeline.evaluate(cfg, con, log=_log)
    table = Table(title="Locked test set")
    for col in ("model", "qini [95% CI]", "uplift@10%", "uplift@30%", "profit/1000 @ depth"):
        table.add_column(col)
    for r in res.itertuples():
        table.add_row(
            r.model,
            f"{r.qini_auc:+.4f} [{r.qini_auc_low:+.4f}, {r.qini_auc_high:+.4f}]",
            f"{r.uplift_at_10:+.2%}",
            f"{r.uplift_at_30:+.2%}",
            f"{r.profit_per_1000_at_depth:,.0f}",
        )
    console.print(table)
    r = res.iloc[0]
    console.print(
        f"depth {r.chosen_depth:.0%} | text-everyone profit/1000: "
        f"{r.profit_text_everyone:,.0f} | noise floor "
        f"[{r.noise_floor_low:+.4f}, {r.noise_floor_high:+.4f}]"
    )


@app.command()
def deploy(config: Path | None = ConfigOpt) -> None:
    """Refit on all labelled clients, calibrate, and score every card holder."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        scores = pipeline.deploy(cfg, con, log=_log)
    console.print(scores.action.value_counts().to_string())


@app.command()
def explain(config: Path | None = ConfigOpt) -> None:
    """Surrogate TreeSHAP drivers of the deployed uplift scores."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        pipeline.explain(cfg, con, log=_log)


@app.command()
def marts(config: Path | None = ConfigOpt) -> None:
    """dbt: BI marts on top of model outputs, then export them to parquet for the dashboard."""
    cfg = _cfg(config)
    _dbt(cfg, "build", "--select", "tag:marts")
    cfg.paths.marts_dir.mkdir(parents=True, exist_ok=True)
    with connect(cfg) as con:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'marts'"
            ).fetchall()
        ]
        for name in tables:
            out = cfg.paths.marts_dir / f"{name}.parquet"
            con.execute(
                f"COPY marts.{name} TO '{out.as_posix()}' (FORMAT parquet, COMPRESSION zstd)"
            )
            console.print(f"  exported {out.relative_to(REPO_ROOT)}")


@app.command()
def run(config: Path | None = ConfigOpt, skip_ingest: bool = False) -> None:
    """Whole pipeline: ingest, transform, train, evaluate, deploy, explain, marts."""
    stages = [
        ("transform", transform),
        ("train", train),
        ("evaluate", evaluate),
        ("deploy", deploy),
        ("explain", explain),
        ("marts", marts),
    ]
    if not skip_ingest:
        stages.insert(0, ("ingest", ingest))
    for name, fn in stages:
        start = time.perf_counter()
        console.rule(f"[bold]{name}")
        fn(config)
        console.print(f"[dim]{name} finished in {time.perf_counter() - start:.0f}s")


if __name__ == "__main__":
    app()
