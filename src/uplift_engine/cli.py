"""`ute` command line. `ute run` executes every stage into one new release and activates it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

from uplift_engine import ingest as ingest_mod
from uplift_engine import pipeline, release
from uplift_engine.config import Config, load_config
from uplift_engine.lineage import (
    Run,
    dataset_fingerprint,
    ensure_meta,
    feature_code_fingerprint,
    feature_schema_fingerprint,
)
from uplift_engine.results import write_results
from uplift_engine.warehouse import connect

app = typer.Typer(add_completion=False, help="Uplift targeting engine pipeline.")
console = Console()
ConfigOpt = typer.Option(
    None, "--config", "-c", help="Pipeline YAML (default configs/pipeline.yaml)"
)


def _cfg(path: Path | None) -> Config:
    return load_config(path)


def _log(msg: str) -> None:
    console.print(msg)


def dbt(cfg: Config, *args: str, extra_vars: dict | None = None) -> None:
    """Run dbt from this environment with as_of (and release_id) passed from the config."""
    exe = shutil.which("dbt", path=str(Path(sys.executable).parent)) or "dbt"
    project = cfg.root / "dbt"
    dbt_vars = {"as_of": cfg.dataset.as_of.strftime("%Y-%m-%d %H:%M:%S"), **(extra_vars or {})}
    env = {**os.environ, "UTE_WAREHOUSE": str(cfg.paths.warehouse)}
    cmd = [
        exe,
        *args,
        "--project-dir",
        str(project),
        "--profiles-dir",
        str(project),
        "--vars",
        json.dumps(dbt_vars),
    ]
    subprocess.run(cmd, check=True, env=env, cwd=project)


@app.command()
def ingest(config: Path | None = ConfigOpt, force: bool = False) -> None:
    """Download the raw files and load them (SHA-256 verified at download and at load)."""
    cfg = _cfg(config)
    ingest_mod.download(cfg, force=force)
    with connect(cfg) as con:
        counts = ingest_mod.load_raw(cfg, con)
    for table, n in counts.items():
        console.print(f"  raw.{table:14s} {n:>12,} rows")


@app.command()
def transform(config: Path | None = ConfigOpt) -> None:
    """dbt: point-in-time staging + feature store with data tests; records the feature build."""
    cfg = _cfg(config)
    dbt(cfg, "build", "--select", "tag:features", "source:raw")
    with connect(cfg) as con:
        ensure_meta(con)
        build = {
            "build_id": f"features-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}",
            "dataset_fp": dataset_fingerprint(con),
            "feature_code_fp": feature_code_fingerprint(cfg.root, cfg.dataset.as_of),
            "schema_fp": feature_schema_fingerprint(con),
        }
        con.execute(
            "INSERT INTO meta.feature_build VALUES (?, ?, ?, ?, ?, ?)",
            [
                build["build_id"],
                datetime.now(UTC),
                build["dataset_fp"],
                build["feature_code_fp"],
                build["schema_fp"],
                cfg.dataset.as_of,
            ],
        )
    console.print(f"  feature build {build['build_id']}")


@app.command()
def assess(config: Path | None = ConfigOpt, new_release: bool = False) -> None:
    """Outer-fold retrospective validation of the complete policy procedure."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        if new_release:
            release.new_release(cfg, con)
        pipeline.assess(cfg, con, log=_log)


@app.command()
def deploy(config: Path | None = ConfigOpt) -> None:
    """Fit the procedure on all labelled customers; score everyone; freeze the campaign."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        pipeline.deploy(cfg, con, log=_log)


@app.command()
def historical(config: Path | None = ConfigOpt) -> None:
    """Corrected intervals for the reused 2026-10-24 test split, from frozen scores."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        pipeline.historical(cfg, con, log=_log)


@app.command()
def explain(config: Path | None = ConfigOpt) -> None:
    """Raw-score attribution with held-out surrogate fidelity."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        pipeline.explain(cfg, con, log=_log)


@app.command()
def marts(config: Path | None = ConfigOpt) -> None:
    """dbt marts for the release being built; export them and results.json into the release."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        rid = release.building_release(cfg, con, create=False)
    dbt(cfg, "build", "--select", "tag:marts", extra_vars={"release_id": rid})
    with connect(cfg) as con, Run(con, cfg, "marts", rid) as run:
        out = release.release_dir(cfg, rid) / "marts"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        tables = [
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'marts'"
            ).fetchall()
        ]
        for name in tables:
            con.execute(
                f"COPY marts.{name} TO '{(out / name).as_posix()}.parquet' "
                "(FORMAT parquet, COMPRESSION zstd)"
            )
        run.details = {"n_marts": len(tables)}
    with connect(cfg) as con:
        write_results(con, rid, release.release_dir(cfg, rid) / "results.json")
    console.print(f"  exported {len(tables)} marts + results.json into release {rid}")


@app.command(name="release")
def release_cmd(config: Path | None = ConfigOpt) -> None:
    """Validate the release being built and make it active (atomic pointer + mart swap)."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        rid = release.building_release(cfg, con, create=False)
        manifest = release.activate(cfg, con, rid)
    console.print(f"[bold green]active release {rid}[/] ({len(manifest['files'])} files)")


@app.command(name="export-send-list")
def export_send_list(out: Path, config: Path | None = ConfigOpt) -> None:
    """Write the active release's send list: action == 'send' only (holdout never included)."""
    import pandas as pd

    cfg = _cfg(config)
    rid, rdir = release.active_release(cfg)
    camp = pd.read_parquet(release.verify_file(rdir, "campaign_assignment.parquet"))
    send = camp[camp.action == "send"][["client_id", "campaign_id", "policy_version"]]
    send.to_csv(out, index=False)
    console.print(f"  {len(send):,} clients to text (release {rid}) -> {out}")


@app.command()
def status(config: Path | None = ConfigOpt) -> None:
    """Show releases and their status."""
    cfg = _cfg(config)
    with connect(cfg, read_only=True) as con:
        print(
            con.execute(
                "SELECT release_id, status, created_at, activated_at "
                "FROM meta.releases ORDER BY created_at"
            )
            .df()
            .to_string(index=False)
        )


@app.command()
def run(config: Path | None = ConfigOpt, skip_ingest: bool = False) -> None:
    """Every stage into a new release, then activate it if validation passes."""
    cfg = _cfg(config)
    stages = [("transform", transform)]
    if not skip_ingest:
        stages.insert(0, ("ingest", ingest))
    for name, fn in stages:
        _stage(name, fn, config)
    with connect(cfg) as con:
        rid = release.new_release(cfg, con)
    console.print(f"[bold]building release {rid}")
    for name, fn in [
        ("assess", assess),
        ("deploy", deploy),
        ("historical", historical),
        ("explain", explain),
        ("marts", marts),
        ("release", release_cmd),
    ]:
        _stage(name, fn, config)


def _stage(name, fn, config) -> None:
    start = time.perf_counter()
    console.rule(f"[bold]{name}")
    fn(config)
    console.print(f"[dim]{name} finished in {time.perf_counter() - start:.0f}s")


if __name__ == "__main__":
    app()
