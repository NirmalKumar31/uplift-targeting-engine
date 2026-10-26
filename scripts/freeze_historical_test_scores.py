"""One-off, logged scoring of the historical test split, saved into the evidence snapshot so
its intervals can be recomputed without refitting.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import joblib
import pandas as pd

from uplift_engine.config import load_config
from uplift_engine.data import load_experiment, load_features
from uplift_engine.models.learners import build_learner

ROOT = Path(__file__).resolve().parents[1]
SNAP = ROOT / "snapshots" / "2026-10-25_pre_remediation"
MANIFEST = ROOT / "docs" / "evidence" / "2026-10-25_pre_remediation_manifest.json"
REASON = (
    "A1 remediation: freeze per-customer scores of the already-selected 2026-10-24 bundle "
    "to recompute intervals with the corrected bootstrap; no selection"
)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    manifest = json.loads(MANIFEST.read_text())
    for rel in (
        "artifacts/model_dev.joblib",
        "artifacts/selection.joblib",
        "tables/ml.split_assignment.parquet",
    ):
        if sha(SNAP / rel) != manifest["files"][rel]["sha256"]:
            raise SystemExit(f"{rel} does not match the snapshot manifest")

    cfg = load_config()
    split = pd.read_parquet(SNAP / "tables" / "ml.split_assignment.parquet").set_index("client_id")
    selection = joblib.load(SNAP / "artifacts" / "selection.joblib")
    con = duckdb.connect(str(cfg.paths.warehouse))
    experiment = load_experiment(con)
    if not experiment.index.sort_values().equals(split.index.sort_values()):
        raise SystemExit("experiment customers differ from the frozen split")
    split = split.loc[experiment.index, "split"]
    dev, test = experiment[split == "dev"], experiment[split == "test"]
    X_dev, X_test = load_features(con, dev.index), load_features(con, test.index)

    con.execute("ALTER TABLE meta.test_access_log ADD COLUMN IF NOT EXISTS reason VARCHAR")
    con.execute("ALTER TABLE meta.test_access_log ADD COLUMN IF NOT EXISTS bundle_sha256 VARCHAR")
    con.execute(
        "INSERT INTO meta.test_access_log (accessed_at, champion, git_sha, reason, bundle_sha256) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            datetime.now(UTC),
            selection["champion"],
            manifest["git_commit"],
            REASON,
            manifest["files"]["artifacts/model_dev.joblib"]["sha256"],
        ],
    )

    champion = joblib.load(SNAP / "artifacts" / "model_dev.joblib")
    params = cfg.models.lightgbm
    t_dev, y_dev = dev.treatment.to_numpy(), dev.target.to_numpy()
    scores = pd.DataFrame(
        {
            "client_id": test.index,
            "treatment": test.treatment.to_numpy(),
            "target": test.target.to_numpy(),
            "champion": champion.predict(X_test),
            "response": build_learner("response", params, cfg.experiment.seed)
            .fit(X_dev, t_dev, y_dev)
            .predict(X_test),
            "random": build_learner("random", params, cfg.experiment.seed)
            .fit(X_dev, t_dev, y_dev)
            .predict(X_test),
        }
    )
    dev_scores = pd.read_parquet(SNAP / "artifacts" / "dev_oof_scores.parquet")
    out = SNAP / "historical_test_scores.parquet"
    scores.to_parquet(out)
    meta = {
        "created_at": datetime.now(UTC).isoformat(),
        "reason": REASON,
        "champion": selection["champion"],
        "dev_chosen_depth": selection["depth"],
        "margin": selection["margin"],
        "n_test": len(scores),
        "sha256": sha(out),
        "dev_oof_rows": len(dev_scores),
        "note": "features = pre-remediation definitions (warehouse as of commit 0033e24)",
    }
    (SNAP / "historical_test_scores.json").write_text(json.dumps(meta, indent=2))
    manifest["files"]["historical_test_scores.parquet"] = {
        "sha256": meta["sha256"],
        "bytes": out.stat().st_size,
    }
    manifest["historical_test_scores"] = meta
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
