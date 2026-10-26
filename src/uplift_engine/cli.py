"""`ute` command line."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich.console import Console

from uplift_engine import ingest as ingest_mod
from uplift_engine import pipeline, release
from uplift_engine.config import Config, ProjectRootError, load_config
from uplift_engine.lineage import Run, begin_feature_build, finish_feature_build
from uplift_engine.results import write_results
from uplift_engine.warehouse import connect

app = typer.Typer(add_completion=False, help="Uplift targeting engine pipeline.")
console = Console()
ConfigOpt = typer.Option(
    None, "--config", "-c", help="Pipeline YAML (default configs/pipeline.yaml)"
)


def _cfg(path: Path | None) -> Config:
    try:
        return load_config(path)
    except ProjectRootError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc


def _log(msg: str) -> None:
    console.print(msg)


def dbt(cfg: Config, *args: str, extra_vars: dict | None = None) -> None:
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
    """Download the raw files and load them into DuckDB."""
    cfg = _cfg(config)
    ingest_mod.download(cfg, force=force)
    with connect(cfg) as con:
        counts = ingest_mod.load_raw(cfg, con)
    for table, n in counts.items():
        console.print(f"  raw.{table:14s} {n:>12,} rows")


@app.command()
def transform(config: Path | None = ConfigOpt) -> None:
    """Build staging tables and the feature store with dbt."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        build_id = begin_feature_build(con, cfg)
    ok = False
    try:
        dbt(cfg, "build", "--select", "tag:features", "source:raw")
        ok = True
    finally:
        with connect(cfg) as con:
            finish_feature_build(con, build_id, ok)
    console.print(f"  feature build {build_id}: succeeded")


@app.command()
def assess(config: Path | None = ConfigOpt, new_release: bool = False) -> None:
    """Outer-fold validation of the full policy procedure."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        if new_release:
            release.new_release(cfg, con)
        pipeline.assess(cfg, con, log=_log)


@app.command()
def deploy(config: Path | None = ConfigOpt) -> None:
    """Fit on all labelled customers, score everyone and freeze the campaign."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        pipeline.deploy(cfg, con, log=_log)


@app.command()
def historical(config: Path | None = ConfigOpt) -> None:
    """Recompute intervals for the historical test split (needs the local snapshot)."""
    cfg = _cfg(config)
    ok, reason = pipeline.historical_available(cfg)
    if not ok:
        console.print(f"[red]historical evidence unavailable: {reason}[/]")
        raise typer.Exit(1)
    with connect(cfg) as con:
        pipeline.historical(cfg, con, log=_log)


@app.command()
def explain(config: Path | None = ConfigOpt) -> None:
    """Feature attribution for the deployed model."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        pipeline.explain(cfg, con, log=_log)


@app.command()
def marts(config: Path | None = ConfigOpt) -> None:
    """Build the BI marts and results.json for the release being built."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        rid = release.building_release(cfg, con, create=False)
        runs = release.latest_runs(con, rid)
    upstream = {s: r for s, r in runs.items() if s in ("assess", "deploy", "explain", "historical")}
    failed = [s for s, r in upstream.items() if r["status"] != "succeeded"]
    missing = [s for s in ("assess", "deploy", "explain") if s not in upstream]
    if failed or missing:
        console.print(f"[red]cannot build marts: missing {missing}, not succeeded {failed}[/]")
        raise typer.Exit(1)
    producers = {s: r["run_id"] for s, r in upstream.items()}
    has_hist = "historical" in producers
    dbt(
        cfg,
        "build",
        "--select",
        "tag:marts",
        extra_vars={
            "release_id": rid,
            "has_historical": has_hist,
            **{f"{s}_run": r for s, r in producers.items()},
        },
    )
    with connect(cfg) as con, Run(con, cfg, "marts", rid, parents=sorted(producers.values())):
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
        (out / "PRODUCERS.json").write_text(
            json.dumps({"release_id": rid, "runs": producers}, indent=2)
        )
        rdir = release.release_dir(cfg, rid)
        write_results(
            con,
            rid,
            producers,
            rdir / "results.json",
            historical_reason=None if has_hist else pipeline.historical_available(cfg)[1],
        )
        shutil.copy2(rdir / "results.json", out / "results.json")
    console.print(f"  exported {len(tables)} marts + results.json into release {rid}")


@app.command(name="release")
def release_cmd(config: Path | None = ConfigOpt) -> None:
    """Validate the release being built and make it active."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        rid = release.building_release(cfg, con, create=False)
        manifest = release.activate(cfg, con, rid)
    console.print(f"[bold green]active release {rid}[/] ({len(manifest['files'])} files)")


@app.command(name="export-send-list")
def export_send_list(out: Path, config: Path | None = ConfigOpt) -> None:
    """Write the active campaign's send list (holdout excluded)."""
    import pandas as pd

    cfg = _cfg(config)
    rid, rdir = release.active_release(cfg)
    camp = pd.read_parquet(release.verify_file(rdir, "campaign_assignment.parquet"))
    send = camp[camp.action == "send"][["client_id", "campaign_id", "policy_version"]]
    send.to_csv(out, index=False)
    console.print(f"  {len(send):,} clients to text (release {rid}) -> {out}")


@app.command()
def status(config: Path | None = ConfigOpt) -> None:
    """Show releases and repair derived state if needed."""
    cfg = _cfg(config)
    with connect(cfg) as con:
        active = release.reconcile(cfg, con)
        release.ensure_table(con)
        print(
            con.execute(
                "SELECT release_id, status, created_at, activated_at "
                "FROM meta.releases ORDER BY created_at"
            )
            .df()
            .to_string(index=False)
        )
    console.print(f"active release: {active}")


@app.command()
def run(config: Path | None = ConfigOpt, skip_ingest: bool = False) -> None:
    """Run every stage into a new release and activate it."""
    cfg = _cfg(config)
    hist_ok, hist_reason = pipeline.historical_available(cfg)
    console.print(
        f"[bold]preflight[/]: historical comparison "
        f"{'included' if hist_ok else 'unavailable'} ({hist_reason})"
    )
    if shutil.which("dbt", path=str(Path(sys.executable).parent)) is None:
        console.print("[red]dbt is not installed in this environment (uv sync --all-extras)[/]")
        raise typer.Exit(1)
    stages = [("transform", transform)]
    if not skip_ingest:
        stages.insert(0, ("ingest", ingest))
    for name, fn in stages:
        _stage(name, fn, config)
    with connect(cfg) as con:
        release.reconcile(cfg, con)
        rid = release.new_release(cfg, con)
    console.print(f"[bold]building release {rid}")
    plan = [("assess", assess), ("deploy", deploy)]
    if hist_ok:
        plan.append(("historical", historical))
    plan += [("explain", explain), ("marts", marts), ("release", release_cmd)]
    for name, fn in plan:
        _stage(name, fn, config)


def _stage(name, fn, config) -> None:
    start = time.perf_counter()
    console.rule(f"[bold]{name}")
    fn(config)
    console.print(f"[dim]{name} finished in {time.perf_counter() - start:.0f}s")


if __name__ == "__main__":
    app()
