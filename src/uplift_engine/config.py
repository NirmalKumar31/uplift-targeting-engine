"""Pipeline configuration (configs/pipeline.yaml)."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

CONFIG_REL = Path("configs") / "pipeline.yaml"
BASELINES = {"random", "response"}
UPLIFT_LEARNERS = {"s_learner", "t_learner", "x_learner", "class_transformation", "dr_learner"}


class ProjectRootError(RuntimeError):
    pass


def project_root(start: Path | None = None) -> Path:
    env = os.environ.get("UTE_ROOT")
    if env:
        root = Path(env).expanduser().resolve()
        if not (root / CONFIG_REL).exists():
            raise ProjectRootError(f"UTE_ROOT={root} has no {CONFIG_REL}")
        return root
    for d in [(start or Path.cwd()).resolve(), *(start or Path.cwd()).resolve().parents]:
        if (d / CONFIG_REL).exists():
            return d
    here = Path(__file__).resolve().parents[2]
    if (here / CONFIG_REL).exists():
        return here
    raise ProjectRootError(
        "cannot find the project root: run from a source checkout or set UTE_ROOT to a directory "
        "containing configs/pipeline.yaml and dbt/"
    )


class Paths(BaseModel):
    raw_dir: Path
    warehouse: Path
    releases_dir: Path
    marts_dir: Path
    historical_snapshot: Path
    historical_manifest: Path

    def resolve(self, root: Path) -> Paths:
        return Paths(**{k: v if v.is_absolute() else root / v for k, v in self})


class Dataset(BaseModel):
    base_url: str
    files: dict[str, str]
    as_of: datetime


class Experiment(BaseModel):
    seed: int = 42
    n_folds: int = Field(5, ge=2)
    outer_folds: int = Field(5, ge=2)
    n_bootstrap: int = Field(500, ge=1)
    noise_rankings: int = Field(200, ge=1)


class Models(BaseModel):
    candidates: list[str] = Field(min_length=1)
    ensemble_top_k: int = Field(3, ge=0)
    lightgbm: dict[str, Any]

    @field_validator("candidates")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        unknown = set(v) - BASELINES - UPLIFT_LEARNERS
        if unknown:
            raise ValueError(f"unknown learners {sorted(unknown)}")
        if len(set(v)) != len(v):
            raise ValueError("duplicate candidates")
        if not set(v) & UPLIFT_LEARNERS:
            raise ValueError("at least one uplift learner is required")
        return v

    @model_validator(mode="after")
    def _ensemble(self) -> Models:
        n = len(set(self.candidates) & UPLIFT_LEARNERS)
        if self.ensemble_top_k == 1 or self.ensemble_top_k > n:
            raise ValueError(f"ensemble_top_k must be 0 or between 2 and {n} (uplift candidates)")
        return self

    @property
    def uplift_candidates(self) -> list[str]:
        return [c for c in self.candidates if c in UPLIFT_LEARNERS]


class Calibration(BaseModel):
    n_bins: int = Field(20, ge=2)


class Economics(BaseModel):
    currency: str = "RUB"
    sms_cost: float = Field(gt=0)
    gross_margin: float = Field(gt=0, le=1)
    margin_per_purchase: float | None = Field(None, gt=0)


class Campaign(BaseModel):
    campaign_id: str = Field(min_length=1)
    holdout_share: float = Field(0.1, ge=0, lt=1)


class Monitoring(BaseModel):
    psi_warn: float = Field(0.1, gt=0)
    psi_fail: float = Field(0.25, gt=0)


class Config(BaseModel):
    paths: Paths
    dataset: Dataset
    experiment: Experiment
    models: Models
    calibration: Calibration = Calibration()
    economics: Economics
    campaign: Campaign
    monitoring: Monitoring
    root: Path | None = None

    def fingerprint_payload(self) -> dict:
        return self.model_dump(mode="json", exclude={"paths", "root"})


def load_config(path: Path | None = None, root: Path | None = None) -> Config:
    """Load `path`, else $UTE_CONFIG, else configs/pipeline.yaml under the project root."""
    root = root or project_root()
    path = path or os.environ.get("UTE_CONFIG") or root / CONFIG_REL
    cfg = Config.model_validate(yaml.safe_load(Path(path).read_text()))
    cfg.paths = cfg.paths.resolve(root)
    cfg.root = root
    return cfg
