from dataclasses import replace

import numpy as np
import pytest

from trustscore import linker, model
from trustscore.evaluate import frames
from trustscore.features import FEATURES, pair_features
from trustscore.simulate import SimConfig, simulate

SMALL = SimConfig(n_honest_operators=15, n_clients=100, n_sybil_operators=2, p_hub_funding=1.0)


@pytest.fixture(scope="module")
def trained():
    return linker.train({"small": SMALL}, seeds=range(100, 102))


def test_pair_features_are_one_row_per_ordered_pair():
    w = simulate(SMALL)
    regs, fb, fund = frames(w)
    f = pair_features(fb, fund, model.detect_hubs(fund, model.ModelConfig()))
    assert list(f.columns) == FEATURES
    assert f.index.is_unique and all(a < b for a, b in f.index)
    assert (f.n_common >= 1).all()
    assert f.jaccard.between(0, 1).all()


def test_linker_separates_operators_on_an_unseen_seed(trained):
    X, y = linker.world_pairs(simulate(replace(SMALL, seed=7)))
    p = trained.predict(X).to_numpy()
    assert y.sum() > 0
    assert p[y == 1].mean() > 0.8 and p[y == 0].mean() < 0.05


def test_learned_clusters_feed_the_model(trained):
    w = simulate(replace(SMALL, seed=7))
    regs, fb, fund = frames(w)
    res = model.score(regs, fb, fund, model.ModelConfig(), trained)
    sybil = res.agents.agent_id.map(w.truth.is_sybil_agent)
    assert res.agents.risk[sybil].mean() > 0.5 > res.agents.risk[~sybil].mean()


def test_save_load_roundtrip(trained, tmp_path):
    path = tmp_path / "linker.joblib"
    linker.save(trained, path)
    loaded = linker.load(path)
    X, _ = linker.world_pairs(simulate(replace(SMALL, seed=8)))
    assert np.allclose(loaded.predict(X), trained.predict(X))
    assert loaded.features == trained.features
