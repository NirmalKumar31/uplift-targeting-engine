"""Generate Qini reference values with scikit-uplift (run in a separate env with sklift 0.5.1):

python scripts/make_qini_reference.py > tests/fixtures/qini_reference.json
"""

import json
import platform

import numpy as np
import sklearn
import sklift
from sklift.metrics import qini_auc_score, uplift_at_k


def case(name, y, score, t, ks=(0.1, 0.3, 0.5)):
    y, score, t = (np.asarray(a) for a in (y, score, t))
    return {
        "name": name,
        "y": y.tolist(),
        "score": score.tolist(),
        "t": t.tolist(),
        "qini_auc": float(qini_auc_score(y, score, t, negative_effect=True)),
        "uplift_at_k": {
            str(k): float(uplift_at_k(y, score, t, strategy="overall", k=k)) for k in ks
        },
    }


rng = np.random.default_rng(20261025)
cases = []

n = 400
t = rng.integers(0, 2, n)
x = rng.random(n)
y = (rng.random(n) < 0.4 + 0.3 * t * x).astype(int)
cases.append(case("continuous_scores_positive_effect", y, x + rng.normal(0, 0.3, n), t))
cases.append(case("heavily_tied_scores", y, np.round(x, 1), t))

y_neg = (rng.random(n) < 0.6 - 0.3 * t * x).astype(int)
cases.append(case("negative_effect", y_neg, x, t))

t_unequal = (rng.random(n) < 0.2).astype(int)
y_u = (rng.random(n) < 0.3 + 0.2 * t_unequal * x).astype(int)
cases.append(case("unequal_arms_20pct_treated", y_u, x + rng.normal(0, 0.2, n), t_unequal))

print(
    json.dumps(
        {
            "provenance": {
                "generator": "scripts/make_qini_reference.py",
                "scikit_uplift": sklift.__version__,
                "scikit_learn": sklearn.__version__,
                "numpy": np.__version__,
                "python": platform.python_version(),
                "definitions": (
                    "qini_auc_score(negative_effect=True); uplift_at_k(strategy='overall')"
                ),
            },
            "cases": cases,
        },
        indent=1,
    )
)
