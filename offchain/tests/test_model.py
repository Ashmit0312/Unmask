from dataclasses import replace

import pandas as pd
import pytest

from trustscore import baselines, model
from trustscore.evaluate import frames
from trustscore.simulate import SimConfig, simulate

SMALL = SimConfig(n_honest_operators=20, n_clients=150, n_sybil_operators=2)


@pytest.fixture(scope="module")
def direct():
    w = simulate(SMALL)
    return w, *frames(w)


def test_hubs_are_exchanges_not_treasuries(direct):
    w, _, _, fund = direct
    hubs = model.detect_hubs(fund, model.ModelConfig())
    assert hubs == w.truth.hubs


def test_puppet_ratings_of_own_agents_are_self_dealing(direct):
    w, regs, fb, fund = direct
    cluster = model.cluster_wallets(fb, fund, regs, model.ModelConfig())
    owner = regs.set_index("agent_id").owner
    for f in fb.itertuples():
        own = w.truth.operator_of_wallet[f.client] == w.truth.operator_of_agent[f.agent_id]
        assert (cluster[f.client] == cluster[owner[f.agent_id]]) == own


def test_model_beats_naive_on_sybil_agents(direct):
    w, regs, fb, fund = direct
    a = model.score(regs, fb, fund).agents.set_index("agent_id")
    naive = baselines.naive_mean(fb)
    sybil = [aid for aid in a.index if w.truth.is_sybil_agent(aid)]
    true = pd.Series(w.truth.agent_quality) * 100
    assert (a.score[sybil] - true[sybil]).abs().mean() < 0.5 * (naive[sybil] - true[sybil]).abs().mean()


def _frame_attack(regs, fb, fund, victim: int, value: float, n: int = 12, ring_no: int = 0):
    """Add a fresh ring of exchange-funded wallets that all rate `victim` with `value` in one burst."""
    hub = fund.funder.value_counts().index[ring_no]
    t0 = int(max(fb.block.max(), fund.block.max())) + 10_000
    ring = [f"0x{ring_no:08x}{i:032x}" for i in range(1, n + 1)]
    fund2 = pd.concat([fund, pd.DataFrame(
        {"wallet": ring, "funder": hub, "amount": 1.0, "block": [t0 + i for i in range(n)], "tx_hash": "0x"})])
    fb2 = pd.concat([fb, pd.DataFrame({
        "agent_id": victim, "client": ring, "index": 1, "value": value, "tag1": "quality", "tag2": "",
        "block": [t0 + 100 + i for i in range(n)], "tx_hash": "0x", "revoked": False})])
    return regs, fb2, fund2


@pytest.mark.parametrize("value", [0.0, 100.0])
def test_a_ring_cannot_move_someone_elses_agent(direct, value):
    w, regs, fb, fund = direct
    honest = [aid for aid in regs.agent_id if not w.truth.is_sybil_agent(aid)]
    # A well-rated honest agent near the middle of the scale, so pushing it either way would show.
    stats = fb[fb.agent_id.isin(honest)].groupby("agent_id").value.agg(["mean", "size"])
    stats = stats[stats["size"] >= 10]
    victim = int((stats["mean"] - 50).abs().idxmin())
    before = model.score(regs, fb, fund).agents.set_index("agent_id").score[victim]
    after = model.score(*_frame_attack(regs, fb, fund, victim, value)).agents.set_index("agent_id").score[victim]
    naive_after = baselines.naive_mean(_frame_attack(regs, fb, fund, victim, value)[1])[victim]
    assert abs(after - before) < 3
    assert abs(naive_after - baselines.naive_mean(fb)[victim]) > 10


def test_iterative_filtering_discounts_an_outlier():
    fb = pd.DataFrame({
        "agent_id": [1, 1, 1, 2, 2, 2],
        "client": ["a", "b", "liar", "a", "b", "liar"],
        "value": [80.0, 82.0, 0.0, 30.0, 28.0, 100.0],
    })
    q = baselines.iterative_filtering(fb)
    assert q[1] > 75 and q[2] < 35
    assert baselines.naive_mean(fb)[1] < 60


def test_a_burst_routed_through_an_exchange_keeps_it_a_hub(direct):
    w, _, _, fund = direct
    cfg = model.ModelConfig()
    hub = fund.funder.value_counts().index[0]
    t0 = int(fund.block.max()) + 10_000
    burst = pd.DataFrame({"wallet": [f"0x{i:040x}" for i in range(1, 31)], "funder": hub, "amount": 1.0,
                          "block": [t0 + i for i in range(30)], "tx_hash": "0x"})
    assert hub in model.detect_hubs(pd.concat([fund, burst]), cfg)


def test_several_minority_rings_cannot_sink_an_agent(direct):
    """Each ring is well under half the victim's ratings; together they would sink a naive average."""
    w, regs, fb, fund = direct
    honest = [aid for aid in regs.agent_id if not w.truth.is_sybil_agent(aid)]
    victim = int(fb[fb.agent_id.isin(honest)].agent_id.value_counts().index[0])
    before = model.score(regs, fb, fund).agents.set_index("agent_id").score[victim]
    attacked = (regs, fb, fund)
    for ring_no in range(3):
        attacked = _frame_attack(*attacked, victim, 0.0, n=6, ring_no=ring_no)
    after = model.score(*attacked).agents.set_index("agent_id").score[victim]
    assert abs(after - before) < 3
    assert baselines.naive_mean(fb)[victim] - baselines.naive_mean(attacked[1])[victim] > 15
