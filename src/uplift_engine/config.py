"""Typed pipeline configuration loaded from configs/pipeline.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "configs" / "pipeline.yaml"


class Paths(BaseModel):
    raw_dir: Path
    warehouse: Path
    artifacts_dir: Path
    marts_dir: Path

    def resolve(self, root: Path) -> Paths:
        return Paths(**{k: v if v.is_absolute() else root / v for k, v in self})


class Dataset(BaseModel):
    base_url: str
    files: dict[str, str]


class Experiment(BaseModel):
    seed: int = 42
    test_share: float = Field(0.2, gt=0, lt=1)
    n_folds: int = Field(5, ge=2)
    n_bootstrap: int = Field(500, ge=10)


class Tuning(BaseModel):
    enabled: bool = False
    n_trials: int = 25


class Models(BaseModel):
    candidates: list[str]
    ensemble_top_k: int = 3
    lightgbm: dict[str, Any]
    tuning: Tuning = Tuning()


class Economics(BaseModel):
    currency: str = "RUB"
    sms_cost: float = Field(gt=0)
    gross_margin: float = Field(gt=0, le=1)
    margin_per_purchase: float | None = None
    holdout_share: float = Field(0.1, ge=0, lt=1)


class Monitoring(BaseModel):
    psi_warn: float = 0.1
    psi_fail: float = 0.25


class Config(BaseModel):
    paths: Paths
    dataset: Dataset
    experiment: Experiment
    models: Models
    economics: Economics
    monitoring: Monitoring


def load_config(path: Path | None = None, root: Path | None = None) -> Config:
    path = path or DEFAULT_CONFIG
    cfg = Config.model_validate(yaml.safe_load(path.read_text()))
    cfg.paths = cfg.paths.resolve(root or REPO_ROOT)
    return cfg
