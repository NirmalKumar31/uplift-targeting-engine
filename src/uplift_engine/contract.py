"""Feature contract shared by training and the API.

Omitted or null features are allowed unless they are core features; wrong types, non-finite
numbers, out-of-range values, unknown categories and unknown keys are rejected.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

CATEGORY_LEVELS = {"gender": ["F", "M", "U"]}
CORE_FEATURES = ("gender", "n_txn", "recency_days", "spend_total", "card_tenure_days")
MIN_COMPLETENESS = 0.6


def _domain(name: str) -> tuple[float, float | None]:
    if name == "age":
        return 14.0, 100.0
    if name == "mean_hour":
        return 0.0, 24.0
    if name.startswith("share_") or name == "top_store_share":
        return 0.0, 1.0
    if name == "txn_trend_30d":
        return 0.0, None
    return 0.0, None


@dataclass
class FeatureSpec:
    name: str
    kind: str  # "numeric" | "boolean" | "category"
    low: float | None = None
    high: float | None = None
    levels: list[str] = field(default_factory=list)


class ContractError(ValueError):
    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__(f"{len(errors)} invalid record(s)")


@dataclass
class FeatureContract:
    specs: list[FeatureSpec]
    core: tuple[str, ...] = CORE_FEATURES
    min_completeness: float = MIN_COMPLETENESS

    @property
    def columns(self) -> list[str]:
        return [s.name for s in self.specs]

    @classmethod
    def from_training(cls, X: pd.DataFrame) -> FeatureContract:
        specs = []
        for col in X.columns:
            if col in CATEGORY_LEVELS:
                specs.append(FeatureSpec(col, "category", levels=CATEGORY_LEVELS[col]))
            elif set(pd.unique(X[col].dropna())) <= {0, 1} and col.startswith(("has_", "redeem_")):
                specs.append(FeatureSpec(col, "boolean"))
            else:
                lo, hi = _domain(col)
                specs.append(FeatureSpec(col, "numeric", lo, hi))
        contract = cls(specs)
        missing_core = [c for c in contract.core if c not in X.columns]
        if missing_core:
            raise ValueError(f"core features absent from training data: {missing_core}")
        contract.check_frame(X)
        return contract

    def check_frame(self, X: pd.DataFrame) -> None:
        for s in self.specs:
            v = X[s.name]
            if s.kind == "numeric":
                vals = v.dropna().astype(float)
                bad = ~np.isfinite(vals) | (vals < s.low if s.low is not None else False)
                if s.high is not None:
                    bad |= vals > s.high
                if bad.any():
                    raise ValueError(
                        f"training column {s.name} violates its domain "
                        f"[{s.low}, {s.high}] in {int(bad.sum())} rows"
                    )
            elif s.kind == "category":
                extra = set(v.dropna().astype(str)) - set(s.levels)
                if extra:
                    raise ValueError(f"training column {s.name} has unknown levels {extra}")

    def validate_records(self, records: list[dict]) -> tuple[pd.DataFrame, list[float]]:
        """Return the model frame and per-record completeness, or raise ContractError."""
        errors, rows, completeness = [], [], []
        known = set(self.columns) | {"client_id"}
        by_name = {s.name: s for s in self.specs}
        for i, rec in enumerate(records):
            rec_errors = []
            if not isinstance(rec, dict):
                errors.append({"index": i, "errors": ["record must be an object"]})
                continue
            unknown = sorted(set(rec) - known)
            if unknown:
                rec_errors.append(f"unknown features: {unknown}")
            row = {}
            for name, spec in by_name.items():
                value = rec.get(name)
                if value is None:
                    row[name] = None
                    continue
                problem = _check(spec, value)
                if problem:
                    rec_errors.append(f"{name}: {problem}")
                else:
                    row[name] = float(value) if spec.kind != "category" else value
            present = sum(row.get(n) is not None for n in self.columns)
            comp = present / len(self.columns)
            missing_core = [c for c in self.core if row.get(c) is None]
            if missing_core:
                rec_errors.append(f"core features missing or null: {missing_core}")
            if comp < self.min_completeness:
                rec_errors.append(
                    f"only {comp:.0%} of features present; at least "
                    f"{self.min_completeness:.0%} required"
                )
            if rec_errors:
                errors.append({"index": i, "errors": rec_errors})
            rows.append(row)
            completeness.append(comp)
        if errors:
            raise ContractError(errors)
        frame = pd.DataFrame(rows, columns=self.columns)
        return to_model_frame(frame, self), completeness


def _check(spec: FeatureSpec, value) -> str | None:
    if spec.kind == "category":
        if not isinstance(value, str) or value not in spec.levels:
            return f"must be one of {spec.levels}"
        return None
    if spec.kind == "boolean":
        if isinstance(value, bool) or (isinstance(value, int) and value in (0, 1)):
            return None
        return "must be true/false"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "must be a number"
    if not math.isfinite(value):
        return "must be finite"
    if spec.low is not None and value < spec.low:
        return f"must be >= {spec.low}"
    if spec.high is not None and value > spec.high:
        return f"must be <= {spec.high}"
    return None


def to_model_frame(frame: pd.DataFrame, contract: FeatureContract) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    for s in contract.specs:
        v = frame[s.name] if s.name in frame else pd.Series([None] * len(frame), index=frame.index)
        if s.kind == "category":
            out[s.name] = pd.Categorical(v, categories=s.levels)
        else:
            out[s.name] = pd.to_numeric(v, errors="raise").astype(float)
    return out
