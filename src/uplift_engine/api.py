"""Real-time scoring API for the deployed uplift model.

    uvicorn uplift_engine.api:app --port 8000

POST /score takes client feature rows and returns the expected uplift and the send decision;
GET /clients/{id} returns the batch score for a known client from the warehouse.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb
import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from uplift_engine.config import load_config
from uplift_engine.data import prepare_features


class ScoreRequest(BaseModel):
    clients: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        max_length=10_000,
        description="Feature rows; a `client_id` key is optional, missing features are null.",
    )


class ClientScore(BaseModel):
    client_id: str | None
    uplift_score: float
    expected_uplift: float
    p_buy_without_sms: float
    p_buy_with_sms: float
    expected_profit: float
    send: bool


class ScoreResponse(BaseModel):
    model: str
    break_even_uplift: float
    scores: list[ClientScore]


@lru_cache(maxsize=1)
def bundle() -> dict[str, Any]:
    path = Path(
        os.environ.get("UTE_MODEL", load_config().paths.artifacts_dir / "model_prod.joblib")
    )
    if not path.exists():
        raise HTTPException(503, f"no deployed model at {path}; run `ute deploy` first")
    return joblib.load(path)


app = FastAPI(
    title="Uplift Targeting Engine",
    version="1.0.0",
    description="Who should get the promo SMS? Causal uplift scores and send decisions.",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/model")
def model_info() -> dict[str, Any]:
    b = bundle()
    return {
        "champion": b["champion"],
        "trained_at": b["trained_at"],
        "git_sha": b["git_sha"],
        "n_features": len(b["features"]),
        "features": b["features"],
        "margin_per_purchase": b["margin"],
        "sms_cost": b["sms_cost"],
        "break_even_uplift": b["sms_cost"] / b["margin"],
    }


@app.post("/score", response_model=ScoreResponse)
def score(req: ScoreRequest) -> ScoreResponse:
    b = bundle()
    features = b["features"]
    frame = pd.DataFrame(req.clients)
    unknown = set(frame.columns) - set(features) - {"client_id"}
    if unknown:
        raise HTTPException(422, f"unknown features: {sorted(unknown)}")
    ids = frame.pop("client_id") if "client_id" in frame else pd.Series([None] * len(frame))
    frame = frame.reindex(columns=features)
    try:
        X = prepare_features(frame)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f"invalid feature values: {exc}") from exc

    raw = b["model"].predict(X)
    uplift = b["calibrator"].transform(raw)
    p0 = b["control_model"].predict_proba(X)[:, 1]
    profit = uplift * b["margin"] - b["sms_cost"]
    return ScoreResponse(
        model=b["champion"],
        break_even_uplift=b["sms_cost"] / b["margin"],
        scores=[
            ClientScore(
                client_id=None if pd.isna(i) else str(i),
                uplift_score=r,
                expected_uplift=u,
                p_buy_without_sms=p,
                p_buy_with_sms=float(np.clip(p + u, 0, 1)),
                expected_profit=pr,
                send=bool(pr > 0),
            )
            for i, r, u, p, pr in zip(ids, raw, uplift, p0, profit, strict=True)
        ],
    )


@app.get("/clients/{client_id}")
def client(client_id: str) -> dict[str, Any]:
    cfg = load_config()
    with duckdb.connect(str(cfg.paths.warehouse), read_only=True) as con:
        row = con.execute("SELECT * FROM ml.scores WHERE client_id = ?", [client_id]).df()
    if row.empty:
        raise HTTPException(404, f"client {client_id} not found")
    return row.iloc[0].to_dict()
