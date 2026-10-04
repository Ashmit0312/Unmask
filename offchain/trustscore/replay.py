"""Replay a simulated World on chain through the canonical ERC-8004 registries.

Each simulated wallet gets a keypair derived from REPLAY_SECRET and its simulated address. Fundings become
native transfers, registrations `register(agentURI)`, ratings `giveFeedback`, all sent in simulated block order.
The manifest maps simulated wallets/agent ids to real ones and carries the ground-truth labels over.

Local, free:  anvil --fork-url https://testnet-rpc.monad.xyz
              python -m trustscore.replay --rpc-url http://127.0.0.1:8545 --fund anvil --out ../data/replay-local
Testnet:      python -m trustscore.replay --fund keystore:monad-deployer --out ../data/replay-testnet

Keys are derivable by anyone who knows REPLAY_SECRET: testnet only, leftover MON in replay wallets is not safe.
"""

import argparse
import getpass
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd
from eth_account import Account
from eth_utils import keccak
from web3 import Web3
from web3.logs import DISCARD

from .config import MONAD_TESTNET, Network, load_abi
from .indexer import Indexer
from .simulate import SimConfig, World, simulate, to_frames

# Small world that replays in a few hundred transactions.
DEMO = SimConfig(
    n_honest_operators=8,
    honest_agents_per_operator=(1, 2),
    n_clients=40,
    ratings_per_client=3.0,
    n_exchanges=3,
    n_sybil_operators=2,
    sybil_agents=(2, 3),
    sybil_puppets=(5, 8),
)

# Fixed gas limits. Monad bills the gas limit, not gas used, so keep these just above measured usage.
GAS = {"transfer": 21_000, "register": 200_000, "feedback": 300_000}  # measured: 175,653 / 271,327
# Every simulated funding moves at least this much, so wallets that never transact still show their funder onchain.
MIN_FUNDING_WEI = 10**15


def derive_key(secret: str, sim_wallet: str) -> bytes:
    return keccak(secret.encode() + bytes.fromhex(sim_wallet[2:]))


@dataclass
class Step:
    sim_block: int
    order: int  # funding before registration before feedback within a simulated block
    kind: str
    index: int  # position in the world's list for that kind


def plan_funding(world: World, gas_price: int, amount_scale: float = 0.0) -> tuple[dict[int, int], dict[str, int]]:
    """Wei per simulated funding, and the balance each root (never-funded) wallet needs to start with.

    Walks fundings newest-first so a wallet's own needs are known before its funder's transfer is sized.
    Every wallet ends up with 20% headroom over the gas and transfers it will pay for.
    """
    fee = {k: g * gas_price for k, g in GAS.items()}
    need: dict[str, int] = defaultdict(int)
    for r in world.registrations:
        need[r.owner] += fee["register"]
    for f in world.feedback:
        need[f.client] += fee["feedback"]

    amounts: dict[int, int] = {}
    for i in reversed(range(len(world.fundings))):
        f = world.fundings[i]
        amounts[i] = max(int(f.amount * amount_scale * 10**18), need[f.wallet] * 6 // 5, MIN_FUNDING_WEI)
        need[f.funder] += amounts[i] + fee["transfer"]

    funded = {f.wallet for f in world.fundings}
    roots = {w: n * 6 // 5 for w, n in need.items() if w not in funded and n > 0}
    return amounts, roots


def ordered_steps(world: World) -> list[Step]:
    s = [Step(f.block, 0, "funding", i) for i, f in enumerate(world.fundings)]
    s += [Step(r.block, 1, "register", i) for i, r in enumerate(world.registrations)]
    s += [Step(f.block, 2, "feedback", i) for i, f in enumerate(world.feedback)]
    return sorted(s, key=lambda x: (x.sim_block, x.order, x.index))


class Replayer:
    def __init__(self, w3: Web3, net: Network, world: World, secret: str, time_scale: float = 100.0,
                 can_mine: bool = False):
        self.w3, self.net, self.world = w3, net, world
        # Simulated blocks per chain block. Gaps between simulated events are kept at this ratio, because the
        # model's burst windows are measured in blocks; can_mine fills gaps with empty blocks (anvil) instead of waiting.
        self.time_scale, self.can_mine = time_scale, can_mine
        self.seed_funder: str | None = None
        self.identity = w3.eth.contract(address=net.identity_registry, abi=load_abi("IIdentityRegistry"))
        self.reputation = w3.eth.contract(address=net.reputation_registry, abi=load_abi("IReputationRegistry"))
        self.keys = {w: derive_key(secret, w) for w in world.truth.operator_of_wallet}
        self.real = {w: Account.from_key(k).address for w, k in self.keys.items()}
        self.chain_id = w3.eth.chain_id
        self.gas_price = int(w3.eth.gas_price * 1.1)  # testnet base fee sits at its 100 gwei floor
        self._nonce: dict[str, int] = {}
        self.agent_map: dict[int, int] = {}
        self.gas_used: dict[str, int] = defaultdict(int)

    # sending

    def _send(self, key: bytes, tx: dict, kind: str):
        sender = Account.from_key(key).address
        if sender not in self._nonce:
            self._nonce[sender] = self.w3.eth.get_transaction_count(sender)
        tx = {**tx, "from": sender, "nonce": self._nonce[sender], "chainId": self.chain_id,
              "gasPrice": self.gas_price, "gas": GAS[kind]}
        signed = Account.sign_transaction(tx, key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        self._nonce[sender] += 1
        rcpt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        if rcpt.status != 1:
            raise RuntimeError(f"{kind} reverted: {Web3.to_hex(tx_hash)}")
        self.gas_used[kind] = max(self.gas_used[kind], rcpt.gasUsed)
        return rcpt

    def _call(self, sim_wallet: str, fn, kind: str):
        tx = fn.build_transaction({"from": self.real[sim_wallet], "gas": GAS[kind], "gasPrice": self.gas_price,
                                   "nonce": 0, "chainId": self.chain_id})
        return self._send(self.keys[sim_wallet], {"to": tx["to"], "data": tx["data"], "value": 0}, kind)

    def seed_roots(self, roots: dict[str, int], fund: str) -> None:
        if fund == "anvil":
            for w, wei in roots.items():
                self.w3.provider.make_request("anvil_setBalance", [self.real[w], hex(wei)])
            return
        name = fund.removeprefix("keystore:")
        path = Path.home() / ".foundry" / "keystores" / name
        key = Account.decrypt(json.loads(path.read_text()), getpass.getpass(f"Password for keystore {name}: "))
        self.seed_funder = Account.from_key(key).address
        for w, wei in roots.items():
            self._send(key, {"to": self.real[w], "value": wei}, "transfer")

    def _pace(self, target: int) -> None:
        """Bring the chain to block target-1 so the next transaction lands at target (or as soon after as possible)."""
        current = self.w3.eth.block_number
        if target - 1 <= current:
            return
        if self.can_mine:
            self.w3.provider.make_request("anvil_mine", [hex(target - 1 - current)])
        else:
            while self.w3.eth.block_number < target - 1:
                time.sleep(0.2)

    def run(self, amounts: dict[int, int], progress_every: int = 50) -> tuple[int, int]:
        w = self.world
        start = self.w3.eth.block_number + 1
        steps = ordered_steps(w)
        sim_start = steps[0].sim_block
        for n, st in enumerate(steps, 1):
            self._pace(start + int((st.sim_block - sim_start) / self.time_scale))
            if st.kind == "funding":
                f = w.fundings[st.index]
                self._send(self.keys[f.funder], {"to": self.real[f.wallet], "value": amounts[st.index]}, "transfer")
            elif st.kind == "register":
                r = w.registrations[st.index]
                rcpt = self._call(r.owner, self.identity.functions.register(r.agent_uri), "register")
                (ev,) = self.identity.events.Registered().process_receipt(rcpt, errors=DISCARD)
                self.agent_map[r.agent_id] = ev.args.agentId
            else:
                f = w.feedback[st.index]
                fn = self.reputation.functions.giveFeedback(
                    self.agent_map[f.agent_id], int(f.value), 0, f.tag1, f.tag2, "", "", b"\x00" * 32
                )
                self._call(f.client, fn, "feedback")
            if n % progress_every == 0 or n == len(steps):
                print(f"  {n}/{len(steps)} txs")
        return start, self.w3.eth.block_number

    # outputs

    def manifest(self, start: int, end: int) -> dict:
        return {
            "chain_id": self.chain_id,
            "rpc_url": self.net.rpc_url,
            "identity_registry": self.net.identity_registry,
            "reputation_registry": self.net.reputation_registry,
            "from_block": start,
            "to_block": end,
            "time_scale": self.time_scale,
            "seed_funder": self.seed_funder,
            "sim_config": {k: v for k, v in vars(self.world.config).items()},
            "agent_map": {str(k): v for k, v in self.agent_map.items()},
            "wallet_map": self.real,
            "max_gas_used": dict(self.gas_used),
        }

    def real_labels(self) -> dict[str, pd.DataFrame]:
        frames = to_frames(self.world)
        agents = frames["labels_agents"].assign(agent_id=lambda d: d.agent_id.map(self.agent_map))
        wallets = frames["labels_wallets"].assign(wallet=lambda d: d.wallet.map(self.real))
        return {"labels_agents": agents, "labels_wallets": wallets}

    def verify(self, start: int, end: int) -> None:
        """Index the replayed range back from chain and check it matches the simulation exactly."""
        ix = Indexer(self.net, self.w3)
        regs, fbs = ix.registrations(start, end), ix.feedback(start, end)
        assert len(regs) == len(self.world.registrations), (len(regs), len(self.world.registrations))
        assert len(fbs) == len(self.world.feedback), (len(fbs), len(self.world.feedback))

        sim = defaultdict(list)
        for f in self.world.feedback:
            sim[(self.agent_map[f.agent_id], self.real[f.client])].append(f.value)
        chain = defaultdict(list)
        for f in fbs:
            chain[(f.agent_id, f.client)].append(f.value)
        assert sim == chain, "replayed feedback differs from simulation"

        sim_fund = {(self.real[f.wallet], self.real[f.funder]) for f in self.world.fundings}
        chain_fund = {(f.wallet, f.funder) for f in ix.fundings(start, end)}
        assert sim_fund == chain_fund, f"{len(sim_fund ^ chain_fund)} fundings differ from simulation"
        print(f"verified: {len(regs)} registrations, {len(fbs)} feedback and {len(chain_fund)} fundings "
              f"read back from blocks {start}..{end}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--rpc-url", default=MONAD_TESTNET.rpc_url)
    p.add_argument("--fund", default="anvil", help="'anvil' (set balances) or 'keystore:<foundry keystore name>'")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--amount-scale", type=float, default=0.0, help="multiply simulated MON amounts (0 = gas only)")
    p.add_argument("--time-scale", type=float, default=100.0, help="simulated blocks per chain block")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    args = p.parse_args()

    net = replace(MONAD_TESTNET, rpc_url=args.rpc_url)
    w3 = Web3(Web3.HTTPProvider(args.rpc_url))
    world = simulate(replace(DEMO, seed=args.seed))
    secret = os.getenv("REPLAY_SECRET", "trustscore-dev")
    rp = Replayer(w3, net, world, secret, args.time_scale, can_mine=args.fund == "anvil")

    amounts, roots = plan_funding(world, rp.gas_price, args.amount_scale)
    n_tx = len(world.fundings) + len(world.registrations) + len(world.feedback)
    cost = sum(roots.values()) / 1e18
    print(f"chain {rp.chain_id}: {len(world.registrations)} agents, {len(world.feedback)} feedback, "
          f"{len(world.fundings)} fundings = {n_tx} txs; {len(roots)} root wallets need {cost:.4f} MON "
          f"at {rp.gas_price / 1e9:.1f} gwei; spans ~{world.config.duration_blocks / args.time_scale:,.0f} chain blocks")
    if args.fund != "anvil" and not args.yes and input("proceed? [y/N] ").strip().lower() != "y":
        return

    rp.seed_roots(roots, args.fund)
    start, end = rp.run(amounts)
    rp.verify(start, end)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "manifest.json").write_text(json.dumps(rp.manifest(start, end), indent=2))
    for name, df in rp.real_labels().items():
        df.to_csv(args.out / f"{name}.csv", index=False)
    print(f"max gas used per kind: {dict(rp.gas_used)} (limits {GAS})")
    print(f"wrote manifest and labels to {args.out}")


if __name__ == "__main__":
    main()
