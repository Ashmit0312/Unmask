from dataclasses import replace

import pandas as pd

from trustscore import model
from trustscore.evaluate import frames
from trustscore.oracle import RING_FLAG, ChainData, cluster_id, extend, model_hash, to_onchain
from trustscore.simulate import SimConfig, simulate

SMALL = SimConfig(n_honest_operators=20, n_clients=150, n_sybil_operators=2)


def test_cluster_id_is_stable_nonzero_uint32():
    a = cluster_id("0xAbC0000000000000000000000000000000000001")
    assert a == cluster_id("0xabc0000000000000000000000000000000000001")
    assert 0 < a < 2**32


def test_model_hash_tracks_config():
    assert model_hash(model.ModelConfig()) == model_hash(model.ModelConfig())
    assert model_hash(model.ModelConfig()) != model_hash(model.ModelConfig(block_scale=100.0))


def test_onchain_rows_fit_the_contract_and_flag_rings():
    w = simulate(SMALL)
    regs, fb, fund = frames(w)
    res = model.score(regs, fb, fund)
    rows = to_onchain(res, regs)
    assert rows.score_bps.between(0, 10_000).all() and rows.confidence_bps.between(0, 10_000).all()
    assert rows.cluster_id.between(0, 2**32 - 1).all()

    sybil = rows.agent_id.map(w.truth.is_sybil_agent)
    assert (rows.cluster_id[sybil] != 0).all(), "every sybil agent carries its operator's cluster id"
    assert (rows.cluster_id[~sybil] == 0).all()
    # One cluster id per sybil operator.
    op = rows.agent_id.map(w.truth.operator_of_agent)
    assert rows[sybil].groupby(op[sybil]).cluster_id.nunique().eq(1).all()
    assert rows[sybil].cluster_id.nunique() == len(w.truth.sybil_operators)
    assert RING_FLAG <= 1


def test_block_scale_undoes_time_compression():
    """A replay that squeezes time 100:1 scores the same once block_scale=100 is applied."""
    w = simulate(SMALL)
    regs, fb, fund = frames(w)
    squeeze = lambda f: f.assign(block=f.block // 100)  # noqa: E731
    base = model.score(regs, fb, fund).agents.set_index("agent_id").score
    squeezed = model.score(squeeze(regs), squeeze(fb), squeeze(fund), model.ModelConfig(block_scale=100.0))
    diff = (squeezed.agents.set_index("agent_id").score - base).abs()
    assert diff.max() < 1.0

    unscaled = model.score(squeeze(regs), squeeze(fb), squeeze(fund)).agents.set_index("agent_id").score
    assert (unscaled - base).abs().max() > 10, "without block_scale the compressed timeline breaks the model"


def test_extend_keeps_first_funding_and_late_revocations():
    cols_fb = ["agent_id", "client", "index", "value", "tag1", "tag2", "block", "tx_hash", "revoked"]
    cols_fund = ["wallet", "funder", "amount", "block", "tx_hash"]
    regs = pd.DataFrame({"agent_id": [1], "owner": ["o"], "agent_uri": [""], "block": [1], "tx_hash": [""]})
    old = ChainData(
        regs,
        pd.DataFrame([[1, "c", 1, 90.0, "", "", 2, "", False]], columns=cols_fb),
        pd.DataFrame([["c", "f1", 1.0, 1, ""]], columns=cols_fund),
        set(), 10,
    )
    new = ChainData(
        regs.iloc[:0],
        pd.DataFrame([[1, "c", 2, 10.0, "", "", 12, "", False]], columns=cols_fb),
        pd.DataFrame([["c", "f2", 1.0, 11, ""]], columns=cols_fund),
        {(1, "c", 1)}, 20,
    )
    merged = extend(old, new)
    assert merged.to_block == 20
    assert merged.fundings.funder.tolist() == ["f1"]
    assert merged.feedback.revoked.tolist() == [True, False]
