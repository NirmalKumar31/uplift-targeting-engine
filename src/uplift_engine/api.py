"""Scoring and campaign-authorization API over the *active release*.

    uvicorn uplift_engine.api:app --port 8000

Endpoints
    GET  /health                         liveness only (the process is up)
    GET  /ready                          readiness: an active release with a hash-verified bundle
    GET  /model                          champion, feature contract, economics, action semantics
    POST /score                          features -> score, calibrated effect, ECONOMIC ELIGIBILITY
    GET  /clients/{client_id}            batch score of a known customer in the active release
    GET  /campaigns/{campaign_id}        frozen campaign manifest
    POST /campaigns/{campaign_id}/authorize   may these customers be texted in this campaign?

`/score` never authorizes a send: `economically_eligible` only says the calibrated effect clears
cost/margin. Authorization comes from the frozen campaign assignment (eligible AND not in the
holdout), which only `/campaigns/{id}/authorize` and `ute export-send-list` read. Customers outside
the frozen campaign population are not authorized; scoring them does not add them to it.

Release activation: the active-release pointer is re-read on each request and artifacts are cached
per release id, so a newly activated release is served without a restart.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from uplift_engine.config import ProjectRootError, load_config
from uplift_engine.contract import ContractError
from uplift_engine.lineage import LineageMismatchError
from uplift_engine.release import ReleaseError, active_release, verify_file

ELIGIBILITY_NOTE = (
    "economic eligibility only (calibrated effect x margin > SMS cost); "
    "not campaign authorization - use POST /campaigns/{id}/authorize"
)


class ScoreRequest(BaseModel):
    clients: list[dict[str, Any]] = Field(..., min_length=1, max_length=10_000)


class ClientScore(BaseModel):
    client_id: str | None
    uplift_score: float
    calibrated_effect: float
    p_buy_without_sms: float
    expected_value_if_texted: float
    economically_eligible: bool
    completeness: float


class ScoreResponse(BaseModel):
    release_id: str
    policy_version: str
    champion: str
    break_even_uplift: float
    note: str
    scores: list[ClientScore]


class AuthorizeRequest(BaseModel):
    client_ids: list[str] = Field(..., min_length=1, max_length=100_000)


class Release:
    def __init__(self, release_id: str, rdir):
        self.release_id = release_id
        self.bundle = joblib.load(verify_file(rdir, "bundle.joblib"))
        self.campaign = pd.read_parquet(verify_file(rdir, "campaign_assignment.parquet"))
        self.campaign = self.campaign.set_index("client_id")
        self.scores = pd.read_parquet(verify_file(rdir, "scores.parquet")).set_index("client_id")
        self.policy_version = str(self.campaign.policy_version.iloc[0])


@lru_cache(maxsize=2)
def _load(release_id: str, rdir: str) -> Release:
    from pathlib import Path

    return Release(release_id, Path(rdir))


def current() -> Release:
    try:
        rid, rdir = active_release(load_config())
        return _load(rid, str(rdir))
    except (ReleaseError, ProjectRootError, FileNotFoundError) as exc:
        raise HTTPException(503, f"model not ready: {exc}") from exc
    except LineageMismatchError as exc:
        raise HTTPException(503, f"release artifacts failed verification: {exc}") from exc


app = FastAPI(
    title="Uplift Targeting Engine",
    version="2.0.0",
    description="Uplift scores, economic eligibility and frozen-campaign authorization.",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/ready")
def ready() -> dict[str, Any]:
    rel = current()
    return {
        "status": "ready",
        "release_id": rel.release_id,
        "policy_version": rel.policy_version,
        "champion": rel.bundle["champion"],
    }


@app.get("/model")
def model_info() -> dict[str, Any]:
    rel = current()
    b, c = rel.bundle, rel.bundle["contract"]
    return {
        "release_id": rel.release_id,
        "policy_version": rel.policy_version,
        "champion": b["champion"],
        "action_semantics": b["action_semantics"],
        "margin_per_purchase": b["margin"],
        "sms_cost": b["sms_cost"],
        "break_even_uplift": b["break_even"],
        "contract": {
            "core_features": list(c.core),
            "min_completeness": c.min_completeness,
            "features": [
                {"name": s.name, "kind": s.kind, "low": s.low, "high": s.high, "levels": s.levels}
                for s in c.specs
            ],
        },
    }


@app.post("/score", response_model=ScoreResponse)
def score(req: ScoreRequest) -> ScoreResponse:
    rel = current()
    b = rel.bundle
    try:
        X, completeness = b["contract"].validate_records(req.clients)
    except ContractError as exc:
        raise HTTPException(
            422, {"message": "invalid feature records", "errors": exc.errors}
        ) from exc
    raw = b["learner"].predict(X)
    effect = b["calibrator"].transform(raw)
    p0 = b["control_model"].predict_proba(X)[:, 1]
    value = effect * b["margin"] - b["sms_cost"]
    ids = [r.get("client_id") for r in req.clients]
    return ScoreResponse(
        release_id=rel.release_id,
        policy_version=rel.policy_version,
        champion=b["champion"],
        break_even_uplift=b["break_even"],
        note=ELIGIBILITY_NOTE,
        scores=[
            ClientScore(
                client_id=None if i is None else str(i),
                uplift_score=float(r),
                calibrated_effect=float(e),
                p_buy_without_sms=float(p),
                expected_value_if_texted=float(v),
                economically_eligible=bool(v > 0),
                completeness=float(c),
            )
            for i, r, e, p, v, c in zip(ids, raw, effect, p0, value, completeness, strict=True)
        ],
    )


@app.get("/clients/{client_id}")
def client(client_id: str) -> dict[str, Any]:
    rel = current()
    if client_id not in rel.scores.index:
        raise HTTPException(404, f"client {client_id} is not in release {rel.release_id}")
    row = rel.scores.loc[client_id]
    return {
        "client_id": client_id,
        "release_id": rel.release_id,
        **{k: jsonable(v) for k, v in row.items()},
        "note": "batch score of the active release; campaign action is authoritative only "
        "via /campaigns/{id}/authorize",
    }


def jsonable(value: Any) -> Any:
    """Serialization boundary for warehouse scalars: every missing marker (None, NaN, pd.NA,
    NaT) becomes JSON null; NumPy scalars become Python scalars; timestamps become ISO strings."""
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return None if pd.isna(value) else pd.Timestamp(value).isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):  # array-like values are not scalars
        pass
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _campaign(rel: Release, campaign_id: str) -> pd.DataFrame:
    if campaign_id not in set(rel.campaign.campaign_id):
        raise HTTPException(
            404, f"campaign {campaign_id} is not frozen in release {rel.release_id}"
        )
    return rel.campaign


@app.get("/campaigns/{campaign_id}")
def campaign(campaign_id: str) -> dict[str, Any]:
    rel = current()
    camp = _campaign(rel, campaign_id)
    counts = camp.action.value_counts().to_dict()
    return {
        "campaign_id": campaign_id,
        "release_id": rel.release_id,
        "policy_version": rel.policy_version,
        "holdout_probability": float(camp.holdout_probability.iloc[0]),
        "assignment_rule": "holdout iff eligible and sha256(campaign_id:client_id)/2^64 < "
        "holdout_probability",
        "counts": counts,
    }


@app.post("/campaigns/{campaign_id}/authorize")
def authorize(campaign_id: str, req: AuthorizeRequest) -> dict[str, Any]:
    rel = current()
    camp = _campaign(rel, campaign_id)
    out = []
    for cid in req.client_ids:
        if cid not in camp.index:
            out.append(
                {
                    "client_id": cid,
                    "authorized": False,
                    "action": None,
                    "reason": "not in the frozen campaign population",
                }
            )
            continue
        action = camp.at[cid, "action"]
        reason = {
            "send": "eligible and not held out",
            "holdout": "randomised control group",
            "ineligible": "calibrated effect does not clear cost/margin",
        }[action]
        out.append(
            {"client_id": cid, "authorized": action == "send", "action": action, "reason": reason}
        )
    return {
        "campaign_id": campaign_id,
        "release_id": rel.release_id,
        "policy_version": rel.policy_version,
        "decisions": out,
    }
