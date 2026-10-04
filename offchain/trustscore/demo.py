"""Live attack demo: a sybil ring pumps a bad agent. ERC-8004's own average is fooled; the trust oracle is not.

Runs on top of a replayed background world (trustscore.replay) with a deployed AgentTrustOracle.

  1. ShadyBot, a low-quality agent, registers; three real users try it and rate it poorly.
  2. Its operator funds a ring of puppet wallets that rate it 95-100.
  3. The ring rates the best honest agent 0 to drag a competitor down.

After each act the script indexes the new blocks, scores, posts to the oracle and prints both views.

Local:  python -m trustscore.demo --rpc-url http://127.0.0.1:8545 --fund anvil --oracle 0x... \\
            --key <updater key> --labels ../data/replay-local
Add --via-exchange to fund the ring through an exchange wallet (no funding trail to the operator),
--pause 0 to run straight through, or --no-score to leave scoring to a separate `trustscore.oracle --watch`.
"""

import argparse
import getpass
import json
import os
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from eth_account import Account
from eth_utils import keccak
from web3 import Web3
from web3.logs import DISCARD

from . import model
from .config import MONAD_TESTNET, load_abi
from .indexer import Indexer
from .oracle import Poster, extend, read_chain, score_and_post
from .replay import DEMO, GAS, Replayer
from .simulate import simulate

GATE = (6000, 6000)  # isTrusted(score >= 60.00, confidence >= 0.6000)


class Demo:
    def __init__(self, args, manifest: dict):
        self.args = args
        self.w3 = Web3(Web3.HTTPProvider(args.rpc_url))
        net = replace(MONAD_TESTNET, rpc_url=args.rpc_url)
        secret = os.getenv("REPLAY_SECRET", "trustscore-dev")
        self.world = simulate(replace(DEMO, seed=manifest["sim_config"]["seed"]))
        self.rp = Replayer(self.w3, net, self.world, secret, manifest["time_scale"], can_mine=args.fund == "anvil")
        self.secret = secret
        self.rng = np.random.default_rng(args.seed)
        self.reputation = self.rp.reputation
        self.oracle = self.w3.eth.contract(address=Web3.to_checksum_address(args.oracle), abi=load_abi("AgentTrustOracle"))
        self.names: dict[int, str] = {}

        self.cfg = model.ModelConfig(block_scale=manifest["time_scale"])
        self.start = manifest["from_block"]
        self.ignore = {manifest["seed_funder"]} if manifest.get("seed_funder") else set()
        self.ix = Indexer(net, self.w3)
        self.poster = Poster(self.w3, args.oracle, args.key) if args.key else None
        self.data = None
        self.deployer_key = None

    # wallets and transactions

    def key(self, label: str) -> bytes:
        return keccak(text=f"{self.secret}:demo:{self.args.seed}:{label}")

    def addr(self, key: bytes) -> str:
        return Account.from_key(key).address

    def top_up(self, address: str, wei: int) -> None:
        """Give a wallet gas money from outside the story (anvil cheat, or the deployer on a live chain)."""
        if self.w3.eth.get_balance(address) >= wei:
            return
        if self.args.fund == "anvil":
            self.w3.provider.make_request("anvil_setBalance", [address, hex(wei)])
            return
        if self.deployer_key is None:
            name = self.args.fund.removeprefix("keystore:")
            ks = json.loads((Path.home() / ".foundry" / "keystores" / name).read_text())
            self.deployer_key = Account.decrypt(ks, getpass.getpass(f"Password for keystore {name}: "))
        self.rp._send(self.deployer_key, {"to": address, "value": wei}, "transfer")

    def transfer(self, from_key: bytes, to: str, wei: int) -> None:
        self.rp._send(from_key, {"to": to, "value": wei}, "transfer")

    def rate(self, key: bytes, agent_id: int, value: int) -> None:
        fn = self.reputation.functions.giveFeedback(agent_id, value, 0, "quality", "", "", "", b"\x00" * 32)
        tx = fn.build_transaction({"from": self.addr(key), "gas": GAS["feedback"], "gasPrice": self.rp.gas_price,
                                   "nonce": 0, "chainId": self.rp.chain_id})
        self.rp._send(key, {"to": tx["to"], "data": tx["data"], "value": 0}, "feedback")

    def register(self, key: bytes, uri: str) -> int:
        fn = self.rp.identity.functions.register(uri)
        tx = fn.build_transaction({"from": self.addr(key), "gas": GAS["register"], "gasPrice": self.rp.gas_price,
                                   "nonce": 0, "chainId": self.rp.chain_id})
        rcpt = self.rp._send(key, {"to": tx["to"], "data": tx["data"], "value": 0}, "register")
        (ev,) = self.rp.identity.events.Registered().process_receipt(rcpt, errors=DISCARD)
        return ev.args.agentId

    def gas_for(self, kind: str, n: int = 1) -> int:
        return GAS[kind] * self.rp.gas_price * n * 6 // 5

    def idle(self, blocks: int) -> None:
        """Let time pass between unrelated actions (only possible on a fork)."""
        if self.rp.can_mine:
            self.w3.provider.make_request("anvil_mine", [hex(blocks)])

    # scoring and views

    def refresh(self) -> None:
        if self.poster is None:  # an external `trustscore.oracle --watch` does the scoring
            before, t0 = self.oracle.functions.epoch().call(), time.time()
            while self.oracle.functions.epoch().call() == before and time.time() - t0 < 120:
                time.sleep(1)
            return
        head = self.w3.eth.block_number
        if self.data is None:
            self.data = read_chain(self.ix, self.start, head, self.ignore)
        elif head > self.data.to_block:
            self.data = extend(self.data, read_chain(self.ix, self.data.to_block + 1, head, self.ignore))
        _, todo, hashes = score_and_post(self.data, self.poster, self.cfg)
        epoch = self.oracle.functions.epoch().call()
        print(f"   oracle: {len(todo)} scores updated in {len(hashes)} tx (epoch {epoch})")

    def view(self, agent_id: int) -> dict:
        clients = self.reputation.functions.getClients(agent_id).call()
        count, value, dec = self.reputation.functions.getSummary(agent_id, clients, "", "").call() if clients else (0, 0, 0)
        s = self.oracle.functions.getScore(agent_id).call()
        return {
            "agent": self.names.get(agent_id, f"agent {agent_id}"),
            "ratings": count,
            "ERC-8004 avg": value / 10**dec if count else float("nan"),
            "trust score": s[0] / 100,
            "confidence": s[1] / 10_000,
            "ring flag": f"#{s[2]}" if s[2] else "-",
            "isTrusted": self.oracle.functions.isTrusted(agent_id, *GATE).call(),
        }

    def table(self, agent_ids: list[int]) -> None:
        df = pd.DataFrame([self.view(a) for a in agent_ids])
        print("\n" + df.to_string(index=False, float_format=lambda x: f"{x:.1f}") + "\n")

    def all_agents(self) -> list[int]:
        return sorted(set(self.data.registrations.agent_id)) if self.data is not None else []

    def leaderboards(self, k: int = 5) -> None:
        df = pd.DataFrame([self.view(a) for a in self.all_agents()])
        naive = df.sort_values("ERC-8004 avg", ascending=False).head(k).agent.tolist()
        trust = df[df.isTrusted].sort_values("trust score", ascending=False).head(k).agent.tolist()
        print(f"   top {k} by ERC-8004 average : {', '.join(naive)}")
        print(f"   top {k} trusted by oracle   : {', '.join(trust)}\n")

    def act(self, title: str) -> None:
        print(f"\n=== {title} " + "=" * max(0, 70 - len(title)))
        if self.args.pause is None:
            input("   [enter] ")
        elif self.args.pause:
            time.sleep(self.args.pause)

    # the story

    def run(self) -> None:
        a = self.args
        self.act("Act 0: the market as it stands")
        self.refresh()
        agents = self.all_agents()
        scores = {aid: self.view(aid) for aid in agents}
        victim = max((aid for aid in agents if scores[aid]["isTrusted"]), key=lambda x: scores[x]["trust score"])
        self.names[victim] = f"TopAgent ({victim})"
        self.leaderboards()

        # Act 1: the operator's treasury sets up ShadyBot's owner wallet; real users try it.
        self.act("Act 1: ShadyBot launches and real users rate it poorly")
        treasury = self.key("treasury")
        hub_key = self.rp.keys[sorted(self.world.truth.hubs)[0]] if a.via_exchange else None
        bankroll = hub_key or treasury
        n_ratings = a.puppets * 2 + 3
        self.top_up(self.addr(bankroll), self.gas_for("feedback", n_ratings) + self.gas_for("transfer", a.puppets + 2)
                    + self.gas_for("register"))
        owner = self.key("owner")
        self.transfer(bankroll, self.addr(owner), self.gas_for("register"))
        shady = self.register(owner, "ipfs://shadybot.json")
        self.names[shady] = f"ShadyBot ({shady})"
        print(f"   ShadyBot registered as agent {shady} by {self.addr(owner)}")

        honest = [w for w, op in self.world.truth.operator_of_wallet.items() if op.startswith("client")]
        for i, sim_wallet in enumerate(honest[:3]):
            self.idle(500)
            k = self.rp.keys[sim_wallet]
            self.top_up(self.addr(k), self.gas_for("feedback"))
            v = int(self.rng.integers(15, 31))
            self.rate(k, shady, v)
            print(f"   real user {self.addr(k)[:10]}... rates ShadyBot {v}")
        self.refresh()
        self.table([shady])

        # Act 2: the ring.
        how = "an exchange wallet" if a.via_exchange else "the operator's treasury"
        self.act(f"Act 2: {a.puppets} puppet wallets funded from {how} pump ShadyBot")
        self.idle(500)
        puppets = [self.key(f"puppet{i}") for i in range(a.puppets)]
        for p in puppets:
            self.transfer(bankroll, self.addr(p), self.gas_for("feedback", 2))
        for p in puppets:
            self.rate(p, shady, int(self.rng.integers(95, 101)))
        print(f"   {a.puppets} puppets rated ShadyBot 95-100")
        self.refresh()
        self.table([shady])
        self.leaderboards()

        # Act 3: the same ring tries to drag the top honest agent down.
        self.act(f"Act 3: the ring rates {self.names[victim]} 0 to sink a competitor")
        self.idle(200)
        for p in puppets:
            self.rate(p, victim, 0)
        print(f"   {a.puppets} puppets rated {self.names[victim]} 0")
        self.refresh()
        self.table([shady, victim])
        self.leaderboards()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--rpc-url", default=MONAD_TESTNET.rpc_url)
    p.add_argument("--fund", default="anvil", help="'anvil' or 'keystore:<foundry keystore name>' for gas top-ups")
    p.add_argument("--oracle", default=os.getenv("TRUST_ORACLE_ADDRESS"), required=False)
    p.add_argument("--key", default=os.getenv("ORACLE_PRIVATE_KEY"), help="updater key; omit with --no-score")
    p.add_argument("--no-score", action="store_true", help="wait for an external oracle --watch instead")
    p.add_argument("--labels", type=Path, required=True, help="replay output dir with manifest.json")
    p.add_argument("--puppets", type=int, default=12)
    p.add_argument("--via-exchange", action="store_true")
    p.add_argument("--seed", type=int, default=1, help="different seeds create a fresh ShadyBot and ring")
    p.add_argument("--pause", type=float, help="seconds between acts; omit to wait for Enter")
    args = p.parse_args()
    if not args.oracle:
        p.error("--oracle (or TRUST_ORACLE_ADDRESS) is required")
    if args.no_score:
        args.key = None
    elif not args.key:
        p.error("--key (or ORACLE_PRIVATE_KEY) is required unless --no-score")

    manifest = json.loads((args.labels / "manifest.json").read_text())
    Demo(args, manifest).run()


if __name__ == "__main__":
    main()
