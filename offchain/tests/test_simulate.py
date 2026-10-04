from dataclasses import replace

import pytest

from trustscore.simulate import SimConfig, naive_report, simulate

SMALL = SimConfig(n_honest_operators=20, n_clients=120, n_sybil_operators=2)


@pytest.fixture(scope="module")
def world():
    return simulate(SMALL)


def test_same_seed_same_world():
    a, b = simulate(SMALL), simulate(SMALL)
    assert a.feedback == b.feedback and a.fundings == b.fundings


def test_no_self_feedback(world):
    owner = {r.agent_id: r.owner for r in world.registrations}
    assert all(f.client != owner[f.agent_id] for f in world.feedback)


def test_feedback_after_registration_and_funding(world):
    reg_block = {r.agent_id: r.block for r in world.registrations}
    funded = {}
    for f in world.fundings:
        funded.setdefault(f.wallet, f.block)
    for f in world.feedback:
        assert f.block > reg_block[f.agent_id]
        assert f.block > funded[f.client], "every rater is funded before it rates"


def test_feedback_index_counts_per_agent_client_pair(world):
    seen: dict[tuple[int, str], int] = {}
    for f in world.feedback:
        key = (f.agent_id, f.client)
        seen[key] = seen.get(key, 0) + 1
        assert f.index == seen[key]


def test_puppet_funding_traces_back_to_its_treasury():
    w = simulate(replace(SMALL, funding_hops=3, p_hub_funding=0.0))
    funder = {f.wallet: f.funder for f in w.fundings}
    op = w.truth.operator_of_wallet
    for f in w.feedback:
        if w.truth.is_sybil_wallet(f.client):
            # Walk up the funding chain: every hop stays inside the same operator until a hub.
            wallet, hops = f.client, 0
            while funder.get(wallet) and funder[wallet] not in w.truth.hubs:
                assert op[funder[wallet]] == op[f.client]
                wallet, hops = funder[wallet], hops + 1
            assert hops == 3


def test_hub_funded_puppets_hide_the_treasury():
    w = simulate(replace(SMALL, p_hub_funding=1.0))
    funder = {f.wallet: f.funder for f in w.fundings}
    puppets = {f.client for f in w.feedback if w.truth.is_sybil_wallet(f.client)}
    assert puppets and all(funder[p] in w.truth.hubs for p in puppets)


def test_attack_fools_the_naive_mean(world):
    rep = naive_report(world).groupby("is_sybil")[["true", "naive"]].mean()
    assert rep.loc[True, "naive"] - rep.loc[True, "true"] > 30
    assert abs(rep.loc[False, "naive"] - rep.loc[False, "true"]) < 5
