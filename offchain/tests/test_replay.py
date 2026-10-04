from collections import defaultdict

from eth_account import Account

from trustscore.replay import DEMO, GAS, derive_key, ordered_steps, plan_funding
from trustscore.simulate import simulate

GWEI = 10**9


def test_keys_are_deterministic_and_distinct():
    world = simulate(DEMO)
    wallets = list(world.truth.operator_of_wallet)
    addrs = {Account.from_key(derive_key("s", w)).address for w in wallets}
    assert len(addrs) == len(wallets)
    assert derive_key("s", wallets[0]) == derive_key("s", wallets[0])
    assert derive_key("s", wallets[0]) != derive_key("t", wallets[0])


def test_steps_fund_before_spend():
    world = simulate(DEMO)
    funded_at, registered_at = {}, {}
    for n, st in enumerate(ordered_steps(world)):
        if st.kind == "funding":
            funded_at.setdefault(world.fundings[st.index].wallet, n)
        elif st.kind == "register":
            r = world.registrations[st.index]
            registered_at[r.agent_id] = n
            assert funded_at[r.owner] < n
        else:
            f = world.feedback[st.index]
            assert funded_at[f.client] < n and registered_at[f.agent_id] < n


def test_no_wallet_runs_dry():
    """Replay the plan as a ledger with worst-case fees (every tx billed its full gas limit)."""
    world = simulate(DEMO)
    price = 100 * GWEI
    amounts, roots = plan_funding(world, price)
    bal = defaultdict(int, roots)
    for st in ordered_steps(world):
        if st.kind == "funding":
            f = world.fundings[st.index]
            payer = f.funder
            bal[payer] -= amounts[st.index] + GAS["transfer"] * price
            bal[f.wallet] += amounts[st.index]
        elif st.kind == "register":
            payer = world.registrations[st.index].owner
            bal[payer] -= GAS["register"] * price
        else:
            payer = world.feedback[st.index].client
            bal[payer] -= GAS["feedback"] * price
        assert bal[payer] >= 0, f"{payer} overdrawn at {st}"
