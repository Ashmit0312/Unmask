"""Learned entity resolution: a classifier over wallet pairs, trained on simulated worlds with known operators.

    python -m trustscore.linker --out trustscore/artifacts/linker.joblib
"""

import argparse
import hashlib
from dataclasses import dataclass, replace
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, precision_score, recall_score

from .features import FEATURES, pair_features
from .simulate import SimConfig, World, simulate

DEFAULT_PATH = Path(__file__).parent / "artifacts" / "linker.joblib"
TRAIN_SEEDS = range(100, 106)  # evaluation uses seeds 0..n, so training never sees an evaluated world


@dataclass
class Linker:
    clf: HistGradientBoostingClassifier
    threshold: float
    trained_on: list[str]
    features: list[str]

    def predict(self, feats: pd.DataFrame) -> pd.Series:
        if feats.empty:
            return pd.Series(dtype=float, index=feats.index)
        return pd.Series(self.clf.predict_proba(feats[self.features].to_numpy(float))[:, 1], index=feats.index)

    def digest(self) -> str:
        return hashlib.sha256(joblib.hashing.hash(self.clf).encode() + str(self.threshold).encode()).hexdigest()


def world_pairs(world: World) -> tuple[pd.DataFrame, np.ndarray]:
    """Features and same-operator labels for one simulated world. Hubs come from the rule-based detector,
    exactly as at inference time."""
    from . import model  # local import: model imports this module
    from .evaluate import frames

    regs, fb, fund = frames(world)
    hubs = model.detect_hubs(fund, model.ModelConfig())
    X = pair_features(fb, fund, hubs)
    op = world.truth.operator_of_wallet
    y = np.array([op[a] == op[b] for a, b in X.index], dtype=int)
    return X, y


def train(
    scenarios: dict[str, SimConfig],
    seeds=TRAIN_SEEDS,
    threshold: float = 0.5,
    seed: int = 0,
    features: list[str] = FEATURES,
) -> Linker:
    Xs, ys = [], []
    for cfg in scenarios.values():
        for s in seeds:
            X, y = world_pairs(simulate(replace(cfg, seed=s)))
            Xs.append(X)
            ys.append(y)
    X, y = pd.concat(Xs), np.concatenate(ys)
    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08, max_leaf_nodes=31,
                                         class_weight="balanced", random_state=seed)
    clf.fit(X[features].to_numpy(float), y)
    return Linker(clf, threshold, sorted(scenarios), list(features))


def pair_report(linker: Linker, worlds: list[World]) -> dict[str, float]:
    Xs, ys = zip(*(world_pairs(w) for w in worlds))
    X, y = pd.concat(Xs), np.concatenate(ys)
    p = linker.predict(X).to_numpy()
    pred = p >= linker.threshold
    return {
        "pairs": len(y), "positives": int(y.sum()),
        "avg_precision": average_precision_score(y, p),
        "precision": precision_score(y, pred, zero_division=0),
        "recall": recall_score(y, pred, zero_division=0),
    }


def save(linker: Linker, path: Path = DEFAULT_PATH) -> None:
    # A plain dict, not the dataclass: pickling the class would tie the file to the module that ran training.
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"clf": linker.clf, "threshold": linker.threshold, "trained_on": linker.trained_on,
                 "features": linker.features}, path)


_cache: dict[Path, Linker] = {}


def load(path: Path = DEFAULT_PATH) -> Linker:
    path = Path(path)
    if path not in _cache:
        d = joblib.load(path)
        if not set(d["features"]) <= set(FEATURES):
            raise ValueError(f"{path} uses features this code no longer computes; retrain with python -m trustscore.linker")
        _cache[path] = Linker(d["clf"], d["threshold"], d["trained_on"], d["features"])
    return _cache[path]


def main() -> None:
    from .evaluate import SCENARIOS

    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=DEFAULT_PATH)
    args = p.parse_args()
    linker = train(SCENARIOS)
    save(linker, args.out)
    print(f"trained on {', '.join(linker.trained_on)} x seeds {TRAIN_SEEDS.start}..{TRAIN_SEEDS.stop - 1}")
    rep = pair_report(linker, [simulate(replace(c, seed=0)) for c in SCENARIOS.values()])
    print("held-out seed 0 pairs: " + ", ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}"
                                                 for k, v in rep.items()))
    print(f"saved {args.out}")


if __name__ == "__main__":
    main()
