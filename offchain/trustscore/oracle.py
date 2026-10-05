"""End-to-end: index ERC-8004 activity from chain, score it, post changed scores to AgentTrustOracle.

Local fork (anvil dev account 1 is the updater the local deploy used):
    python -m trustscore.oracle --rpc-url http://127.0.0.1:8545 --oracle 0x... --from-block N \\
        --key 0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d --labels ../data/replay-local
Live demo: add --watch 5 to re-index, re-score and post every 5 seconds.

Testnet reads ORACLE_PRIVATE_KEY and TRUST_ORACLE_ADDRESS from .env when --key / --oracle are omitted.

Real agents on Monad testnet, full history from Envio HyperSync (only agents with ratings are posted):
    python -m trustscore.oracle --source hypersync --watch 30
"""

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd
from eth_account import Account
from eth_utils import keccak
from web3 import Web3

from . import model
from .config import MONAD_TESTNET, Network, load_abi
from .evaluate import frame_metrics
from .indexer import Indexer

RING_FLAG = 0.5  # an agent carries its owner's cluster id onchain when that cluster's ring suspicion reaches this


def model_hash(cfg: model.ModelConfig) -> bytes:
    return keccak(text="trustscore-v1:" + json.dumps(asdict(cfg), sort_keys=True))


def cluster_id(cluster_root: str) -> int:
    """Stable nonzero uint32 for a cluster, so the same operator keeps its id across runs."""
    return int.from_bytes(keccak(text=cluster_root.lower())[:4], "big") or 1


@dataclass
class ChainData:
    registrations: pd.DataFrame
    feedback: pd.DataFrame
    fundings: pd.DataFrame
    revoked: set[tuple[int, str, int]]
    to_block: int


def read_chain(ix: Indexer, start: int, end: int, ignore_funders: set[str] = frozenset()) -> ChainData:
    revoked = ix.revocations(start, end)
    regs = pd.DataFrame(map(asdict, ix.registrations(start, end)),
                        columns=["agent_id", "owner", "agent_uri", "block", "tx_hash"])
    fb = pd.DataFrame(map(asdict, ix.feedback(start, end, revoked)),
                      columns=["agent_id", "client", "index", "value", "tag1", "tag2", "block", "tx_hash", "revoked"])
    fund = pd.DataFrame(map(asdict, ix.fundings(start, end)), columns=["wallet", "funder", "amount", "block", "tx_hash"])
    # Infrastructure wallets (e.g. whoever seeded a replay) are not identity evidence.
    fund = fund[~fund.funder.isin(ignore_funders)]
    return ChainData(regs, fb, fund, revoked, end)


def extend(old: ChainData, new: ChainData) -> ChainData:
    """Append a later block range: keep each wallet's first funding, apply revocations to earlier feedback too."""
    revoked = old.revoked | new.revoked
    fb = pd.concat([old.feedback, new.feedback], ignore_index=True)
    keys = list(zip(fb.agent_id, fb.client, fb["index"]))
    fb["revoked"] = [k in revoked for k in keys]
    fund = pd.concat([old.fundings, new.fundings], ignore_index=True).drop_duplicates("wallet", keep="first")
    regs = pd.concat([old.registrations, new.registrations], ignore_index=True)
    return ChainData(regs, fb, fund, revoked, new.to_block)


def to_onchain(res: model.ScoreResult, regs: pd.DataFrame) -> pd.DataFrame:
    """Model output as the integers AgentTrustOracle stores: basis points and a uint32 cluster id."""
    a = res.agents.merge(regs[["agent_id", "owner"]], on="agent_id")
    w = res.wallets.set_index("wallet")
    owner_cluster = a.owner.map(w.cluster)
    # Flag the operator when its wallets act as a ring, or when the agent's ratings come mostly from its own operator.
    flagged = (a.owner.map(w.suspicion).fillna(0) >= RING_FLAG) | (a.self_dealing_share >= RING_FLAG)
    return pd.DataFrame({
        "agent_id": a.agent_id.astype(int),
        "score_bps": np.clip(np.rint(a.score * 100), 0, 10_000).astype(int),
        "confidence_bps": np.clip(np.rint(a.confidence * 10_000), 0, 10_000).astype(int),
        "cluster_id": [cluster_id(c) if f else 0 for c, f in zip(owner_cluster, flagged)],
    })


class Poster:
    def __init__(self, w3: Web3, oracle: str, key: str):
        self.w3 = w3
        self.acct = Account.from_key(key)
        self.oracle = w3.eth.contract(address=Web3.to_checksum_address(oracle), abi=load_abi("AgentTrustOracle"))

    def current(self, agent_ids: list[int]) -> pd.DataFrame:
        rows = []
        for aid in agent_ids:
            s = self.oracle.functions.getScore(aid).call()
            rows.append({"agent_id": aid, "score_bps": s[0], "confidence_bps": s[1], "cluster_id": s[2], "epoch": s[3]})
        return pd.DataFrame(rows, columns=["agent_id", "score_bps", "confidence_bps", "cluster_id", "epoch"])

    def changed(self, rows: pd.DataFrame, min_score_bps: int = 50, min_confidence_bps: int = 100) -> pd.DataFrame:
        """Rows worth a write: never posted, ring flag changed, or score/confidence moved past the thresholds.
        Small drifts (every new vote nudges the global prior) are skipped: Monad bills the gas limit per write."""
        cur = self.current(rows.agent_id.tolist()).set_index("agent_id").loc[rows.agent_id]
        r = rows.set_index("agent_id")
        diff = (
            (cur.epoch == 0)
            | (r.cluster_id != cur.cluster_id)
            | ((r.score_bps - cur.score_bps).abs() >= min_score_bps)
            | ((r.confidence_bps - cur.confidence_bps).abs() >= min_confidence_bps)
        )
        return rows[diff.to_numpy()]

    def post(self, rows: pd.DataFrame, mhash: bytes, batch: int = 100) -> list[str]:
        hashes = []
        for i in range(0, len(rows), batch):
            b = rows.iloc[i : i + batch]
            fn = self.oracle.functions.postScores(
                b.agent_id.tolist(), b.score_bps.tolist(), b.confidence_bps.tolist(), b.cluster_id.tolist(), mhash
            )
            # Monad bills the gas limit, so size it from an estimate rather than a fixed ceiling.
            gas = int(fn.estimate_gas({"from": self.acct.address}) * 1.15)
            tx = fn.build_transaction({
                "from": self.acct.address, "nonce": self.w3.eth.get_transaction_count(self.acct.address),
                "gas": gas, "gasPrice": int(self.w3.eth.gas_price * 1.1), "chainId": self.w3.eth.chain_id,
            })
            h = self.w3.eth.send_raw_transaction(self.acct.sign_transaction(tx).raw_transaction)
            rcpt = self.w3.eth.wait_for_transaction_receipt(h, timeout=180)
            if rcpt.status != 1:
                raise RuntimeError(f"postScores reverted: {Web3.to_hex(h)}")
            hashes.append(Web3.to_hex(h))
        return hashes


def label_report(onchain: pd.DataFrame, labels_dir: Path) -> dict[str, float]:
    """Score what the oracle actually stores against the replay's ground truth."""
    labels = pd.read_csv(labels_dir / "labels_agents.csv")
    df = labels.merge(onchain, on="agent_id")
    df = df.assign(true=df.quality * 100, sybil=df.is_sybil.astype(bool),
                   score=df.score_bps / 100, confidence=df.confidence_bps / 10_000)
    return frame_metrics(df)


def score_and_post(data: ChainData, poster: Poster, cfg: model.ModelConfig):
    res = model.score(data.registrations, data.feedback, data.fundings, cfg)
    rows = to_onchain(res, data.registrations)
    todo = poster.changed(rows)
    hashes = poster.post(todo, model_hash(cfg)) if len(todo) else []
    return rows, todo, hashes


def run_hypersync(args, poster: Poster) -> None:
    """Score every rated agent on the chain from HyperSync data and keep the oracle in sync."""
    from . import realdata

    net_name = {10143: "monad-testnet", 143: "monad"}[poster.w3.eth.chain_id]
    cfg = model.ModelConfig()
    print(f"oracle {poster.oracle.address} on {net_name}, updater {poster.acct.address}, "
          f"model {Web3.to_hex(model_hash(cfg))[:18]}")
    while True:
        t0 = time.time()
        inp = realdata.collect(net_name, refresh=True, quiet=True)
        res = model.score(inp.registrations, inp.feedback, inp.fundings, cfg, known_hubs=inp.known_hubs)
        rated = set(inp.feedback.agent_id)
        rows = to_onchain(res, inp.registrations)
        rows = rows[rows.agent_id.isin(rated)]  # unrated agents stay unscored: isTrusted() is false for them
        todo = poster.changed(rows)
        hashes = poster.post(todo, model_hash(cfg)) if len(todo) else []
        print(f"[to block {inp.head}] {len(rated)} rated agents, {len(inp.feedback)} ratings -> posted {len(todo)} "
              f"changed scores in {len(hashes)} tx ({time.time() - t0:.1f}s), epoch {poster.oracle.functions.epoch().call()}")
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            rows.to_csv(args.out, index=False)
        if not args.watch:
            break
        time.sleep(args.watch)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source", choices=["rpc", "hypersync"], default="rpc",
                   help="rpc: index a block range (replays); hypersync: full history of real agents")
    p.add_argument("--rpc-url", default=MONAD_TESTNET.rpc_url)
    p.add_argument("--oracle", default=os.getenv("TRUST_ORACLE_ADDRESS"))
    p.add_argument("--key", default=os.getenv("ORACLE_PRIVATE_KEY"))
    p.add_argument("--from-block", type=int, help="defaults to the manifest's from_block when --labels is given")
    p.add_argument("--labels", type=Path, help="replay output dir: manifest.json + labels_agents.csv")
    p.add_argument("--watch", type=float, help="seconds between passes; omit for a single pass")
    p.add_argument("--out", type=Path, help="write the posted score snapshot here as CSV")
    args = p.parse_args()
    if not (args.oracle and args.key):
        p.error("--oracle and --key (or TRUST_ORACLE_ADDRESS / ORACLE_PRIVATE_KEY in .env) are required")

    if args.source == "hypersync":
        return run_hypersync(args, Poster(Web3(Web3.HTTPProvider(args.rpc_url)), args.oracle, args.key))

    manifest = json.loads((args.labels / "manifest.json").read_text()) if args.labels else {}
    start = args.from_block if args.from_block is not None else manifest.get("from_block")
    if start is None:
        p.error("--from-block is required without --labels")
    ignore = {manifest["seed_funder"]} if manifest.get("seed_funder") else set()

    net: Network = replace(MONAD_TESTNET, rpc_url=args.rpc_url)
    w3 = Web3(Web3.HTTPProvider(args.rpc_url))
    cfg = model.ModelConfig(block_scale=manifest.get("time_scale", 1.0))
    ix, poster = Indexer(net, w3), Poster(w3, args.oracle, args.key)
    print(f"oracle {poster.oracle.address}, updater {poster.acct.address}, model {Web3.to_hex(model_hash(cfg))[:18]}")

    data = None
    while True:
        t0 = time.time()
        head = w3.eth.block_number
        if data is None:
            data = read_chain(ix, start, head, ignore)
        elif head > data.to_block:  # only index what is new since the last pass
            data = extend(data, read_chain(ix, data.to_block + 1, head, ignore))
        rows, todo, hashes = score_and_post(data, poster, cfg)
        print(f"[blocks {start}..{head}] {len(data.registrations)} agents, {len(data.feedback)} feedback, "
              f"{len(data.fundings)} fundings -> posted {len(todo)} changed scores in {len(hashes)} tx "
              f"({time.time() - t0:.1f}s), epoch {poster.oracle.functions.epoch().call()}")
        if args.labels:
            stored = poster.current(rows.agent_id.tolist())
            m = label_report(stored, args.labels)
            print("  vs ground truth: " + ", ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}"
                                                    for k, v in m.items()))
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            rows.to_csv(args.out, index=False)
        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
