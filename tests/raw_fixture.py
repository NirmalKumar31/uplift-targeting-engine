"""Small deterministic dataset in the raw X5 schema, including edge cases and broken variants."""

from __future__ import annotations

import gzip
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

AS_OF = datetime(2019, 3, 19)
SPECIAL = {
    "teen": "c000000",
    "bad_age": "c000001",
    "late_redeem": "c000002",
    "early_redeem": "c000003",
    "no_redeem": "c000004",
}


def _write(df: pd.DataFrame, path: Path) -> str:
    with gzip.GzipFile(path, "wb", mtime=0) as fh:  # mtime=0: byte-identical across runs
        fh.write(df.to_csv(index=False).encode())
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_raw(
    path: Path,
    variant: str | None = None,
    n_labelled: int = 1600,
    n_unlabelled: int = 400,
    seed: int = 7,
) -> dict[str, str]:
    rng = np.random.default_rng(seed)
    path.mkdir(parents=True, exist_ok=True)
    n = n_labelled + n_unlabelled
    ids = [f"c{i:06d}" for i in range(n)]
    issue = [AS_OF - timedelta(days=int(d)) for d in rng.integers(60, 700, n)]
    redeem = [
        d + timedelta(days=int(x)) if rng.random() < 0.85 else None
        for d, x in zip(issue, rng.integers(1, 50, n), strict=True)
    ]
    age = rng.integers(18, 85, n).astype(float)
    clients = pd.DataFrame(
        {
            "client_id": ids,
            "first_issue_date": issue,
            "first_redeem_date": redeem,
            "age": age,
            "gender": rng.choice(["F", "M", "U"], n),
        }
    )
    idx = {k: ids.index(v) for k, v in SPECIAL.items()}
    clients.loc[idx["teen"], "age"] = 16
    clients.loc[idx["bad_age"], "age"] = 1901
    clients.loc[idx["late_redeem"], "first_redeem_date"] = AS_OF + timedelta(days=90)
    clients.loc[idx["early_redeem"], "first_redeem_date"] = clients.loc[
        idx["early_redeem"], "first_issue_date"
    ] - timedelta(days=10)
    clients.loc[idx["no_redeem"], "first_redeem_date"] = None
    clients["age"] = clients.age.astype(int)

    products = pd.DataFrame(
        {
            "product_id": [f"p{i:04d}" for i in range(60)],
            "level_1": rng.choice(["e344ab2e71", "c3d3a8e8c6", "ec62ce61e3"], 60),
            "level_2": "l2",
            "level_3": [f"l3_{i % 12}" for i in range(60)],
            "level_4": "l4",
            "segment_id": 1.0,
            "brand_id": "b",
            "vendor_id": "v",
            "netto": rng.random(60).round(2),
            "is_own_trademark": rng.integers(0, 2, 60),
            "is_alcohol": rng.integers(0, 2, 60),
        }
    )

    promo_user = rng.random(n) < 0.3
    lines = []
    for i, cid in enumerate(ids):
        for j in range(int(rng.integers(2, 9))):
            ts = AS_OF - timedelta(days=float(rng.uniform(0.01, 100)))
            express = float(rng.integers(5, 40)) if promo_user[i] and rng.random() < 0.5 else 0.0
            items = rng.integers(1, 5)
            values = rng.uniform(20, 300, items).round(2)
            for k, v in enumerate(values):
                lines.append(
                    {
                        "client_id": cid,
                        "transaction_id": f"t{i:06d}_{j:02d}",
                        "transaction_datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "regular_points_received": 5.0,
                        "express_points_received": 0.0,
                        "regular_points_spent": 0.0,
                        "express_points_spent": -express,
                        "purchase_sum": float(values.sum().round(2)),
                        "store_id": f"s{i % 9}",
                        "product_id": f"p{int(rng.integers(0, 60)):04d}",
                        "product_quantity": 1.0,
                        "trn_sum_from_iss": float(v),
                        "trn_sum_from_red": None if k else 0.0,
                    }
                )
    purchases = pd.DataFrame(lines)

    labelled = ids[:n_labelled]
    t = rng.integers(0, 2, n_labelled)
    tau = 0.30 * promo_user[:n_labelled] - 0.15 * (age[:n_labelled] > 70)
    y = (rng.random(n_labelled) < np.clip(0.35 + t * tau, 0, 1)).astype(int)
    train = pd.DataFrame({"client_id": labelled, "treatment_flg": t, "target": y})
    test = pd.DataFrame({"client_id": ids[n_labelled:]})

    if variant == "future_transaction":
        row = purchases.iloc[[0]].copy()
        row["transaction_id"] = "t_future"
        row["transaction_datetime"] = (AS_OF + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        purchases = pd.concat([purchases, row])
    elif variant == "contradictory":
        two = (
            purchases.groupby("transaction_id").filter(lambda g: len(g) > 1).transaction_id.iloc[0]
        )
        first = purchases.index[purchases.transaction_id == two][0]
        purchases.loc[first, "purchase_sum"] += 999
    elif variant == "unmatched_product":
        purchases.loc[purchases.index[0], "product_id"] = "p_unknown"
    elif variant == "null_transaction_ts":
        purchases.loc[purchases.index[0], "transaction_datetime"] = None
    elif variant is not None:
        raise ValueError(f"unknown variant {variant}")

    fmt = "%Y-%m-%d %H:%M:%S"
    for col in ("first_issue_date", "first_redeem_date"):
        clients[col] = clients[col].map(lambda d: d.strftime(fmt) if pd.notna(d) else None)
    return {
        "clients.csv.gz": _write(clients, path / "clients.csv.gz"),
        "products.csv.gz": _write(products, path / "products.csv.gz"),
        "purchases.csv.gz": _write(purchases, path / "purchases.csv.gz"),
        "uplift_train.csv.gz": _write(train, path / "uplift_train.csv.gz"),
        "uplift_test.csv.gz": _write(test, path / "uplift_test.csv.gz"),
    }


def make_historical_snapshot(snapshot: Path, manifest: Path, seed: int = 11) -> None:
    import json

    rng = np.random.default_rng(seed)
    n = 600
    t = rng.integers(0, 2, n)
    x = rng.random(n)
    y = (rng.random(n) < 0.4 + 0.2 * t * x).astype(int)
    snapshot.mkdir(parents=True, exist_ok=True)
    scores = pd.DataFrame(
        {
            "client_id": [f"h{i}" for i in range(n)],
            "treatment": t,
            "target": y,
            "champion": x + rng.normal(0, 0.3, n),
            "response": rng.random(n),
            "random": rng.random(n),
        }
    )
    path = snapshot / "historical_test_scores.parquet"
    scores.to_parquet(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "files": {"historical_test_scores.parquet": {"sha256": digest}},
                "historical_test_scores": {
                    "champion": "fixture",
                    "dev_chosen_depth": 0.4,
                    "margin": 100.0,
                    "sha256": digest,
                },
            }
        )
    )
