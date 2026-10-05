"""Envio HyperSync data source: full-history ERC-8004 events and wallet funding on Monad, in seconds.

Same records as the RPC indexer (schema.py), plus what an RPC cannot answer:
  - first funding of any wallet across the whole chain (transactions selected by recipient)
  - how many value transfers a funder has ever sent (exchanges send thousands, operator treasuries a few dozen)

HyperSync has no traces for Monad, so wallets funded through a contract call (faucets, batch senders) come back
unresolved; enrich.py fills those from Nansen (mainnet) or BlockVision.

    python -m trustscore.hypersync_source --net monad
"""

import argparse
import asyncio
import os
from collections.abc import Iterable

import hypersync as h

from .config import MONAD_MAINNET, MONAD_TESTNET, Network
from .schema import AgentRegistered, Feedback, Funding

HYPERSYNC_URLS = {143: "https://monad.hypersync.xyz", 10143: "https://monad-testnet.hypersync.xyz"}

SIG_REGISTERED = "Registered(uint256 indexed agentId, string agentURI, address indexed owner)"
SIG_FEEDBACK = (
    "NewFeedback(uint256 indexed agentId, address indexed clientAddress, uint64 feedbackIndex, int128 value, "
    "uint8 valueDecimals, string indexed indexedTag1, string tag1, string tag2, string endpoint, string feedbackURI, "
    "bytes32 feedbackHash)"
)
SIG_REVOKED = "FeedbackRevoked(uint256 indexed agentId, address indexed clientAddress, uint64 indexed feedbackIndex)"
SIG_PASSKEY = ("PasskeyReview(uint256 indexed agentId, bytes32 indexed reviewer, uint8 value, bytes32 tag, uint32 version, "
               "bytes32 x, bytes32 y)")

LOG_FIELDS = [f.value for f in (h.LogField.BLOCK_NUMBER, h.LogField.TRANSACTION_HASH, h.LogField.LOG_INDEX,
                                h.LogField.ADDRESS, h.LogField.TOPIC0, h.LogField.TOPIC1, h.LogField.TOPIC2,
                                h.LogField.TOPIC3, h.LogField.DATA)]
TX_FIELDS = [f.value for f in (h.TransactionField.BLOCK_NUMBER, h.TransactionField.HASH, h.TransactionField.FROM,
                               h.TransactionField.TO, h.TransactionField.VALUE, h.TransactionField.STATUS)]


def _chunks(items: list, n: int) -> Iterable[list]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


class HyperSyncSource:
    def __init__(self, net: Network = MONAD_MAINNET, token: str | None = None):
        self.net = net
        token = token or os.environ.get("ENVIO_API_TOKEN")
        if not token:
            raise RuntimeError("ENVIO_API_TOKEN is not set (https://app.envio.dev/api-tokens)")
        self.client = h.HypersyncClient(h.ClientConfig(url=HYPERSYNC_URLS[net.chain_id], bearer_token=token))
        self._decoders = {sig: h.Decoder([sig]) for sig in (SIG_REGISTERED, SIG_FEEDBACK, SIG_REVOKED, SIG_PASSKEY)}

    # paging

    async def _collect(self, query: h.Query, end: int | None, kind: str) -> list:
        out = []
        while True:
            r = await self.client.get(query)
            out.extend(getattr(r.data, kind))
            stop = r.archive_height if end is None else min(end + 1, r.archive_height)
            if r.next_block >= stop:
                return out
            query.from_block = r.next_block

    async def _events(self, address: str, sig: str, start: int, end: int | None):
        q = h.Query(from_block=start, to_block=None if end is None else end + 1,
                    logs=[h.LogSelection(address=[address], topics=[[h.signature_to_topic0(sig)]])],
                    field_selection=h.FieldSelection(log=LOG_FIELDS))
        logs = await self._collect(q, end, "logs")
        decoded = self._decoders[sig].decode_logs_sync(logs) if logs else []
        return [(log, d) for log, d in zip(logs, decoded) if d is not None]

    # ERC-8004 events (start/end inclusive; end=None means chain head)

    def registrations(self, start: int = 0, end: int | None = None) -> list[AgentRegistered]:
        rows = asyncio.run(self._events(self.net.identity_registry, SIG_REGISTERED, start, end))
        return [AgentRegistered(int(d.indexed[0].val), _addr(d.indexed[1].val), d.body[0].val, log.block_number,
                                log.transaction_hash) for log, d in rows]

    def revocations(self, start: int = 0, end: int | None = None) -> set[tuple[int, str, int]]:
        rows = asyncio.run(self._events(self.net.reputation_registry, SIG_REVOKED, start, end))
        return {(int(d.indexed[0].val), _addr(d.indexed[1].val), int(d.indexed[2].val)) for _, d in rows}

    def feedback(self, start: int = 0, end: int | None = None,
                 revoked: set[tuple[int, str, int]] | None = None) -> list[Feedback]:
        if revoked is None:
            revoked = self.revocations(start, end)
        rows = asyncio.run(self._events(self.net.reputation_registry, SIG_FEEDBACK, start, end))
        out = []
        for log, d in rows:
            agent, client = int(d.indexed[0].val), _addr(d.indexed[1].val)
            index, value, decimals, tag1, tag2 = (v.val for v in d.body[:5])
            out.append(Feedback(agent, client, int(index), int(value) / 10 ** int(decimals), tag1, tag2,
                                log.block_number, log.transaction_hash, (agent, client, int(index)) in revoked))
        return out

    def passkey_reviews(self, contract: str, start: int = 0, end: int | None = None) -> list[Feedback]:
        """Latest review per (passkey, agent) from a PasskeyReviews contract, as Feedback rows. The reviewer's
        pseudonymous address is the low 20 bytes of its passkey id; tag2 is "passkey"; index is the revision."""
        rows = asyncio.run(self._events(contract, SIG_PASSKEY, start, end))
        latest: dict[tuple[int, str], Feedback] = {}
        for log, d in rows:
            agent, reviewer = int(d.indexed[0].val), d.indexed[1].val
            value, tag, version = int(d.body[0].val), d.body[1].val, int(d.body[2].val)
            tag = bytes.fromhex(tag[2:]).rstrip(b"\x00").decode(errors="replace") if isinstance(tag, str) else ""
            client = _addr("0x" + reviewer[-40:])
            latest[(agent, client)] = Feedback(agent, client, version, float(value), tag, "passkey",
                                               log.block_number, log.transaction_hash)
        return list(latest.values())

    # wallet funding

    async def _first_fundings(self, wallets: list[str], end: int | None, start: int = 0) -> dict[str, Funding]:
        found: dict[str, Funding] = {}
        for chunk in _chunks(sorted({w.lower() for w in wallets}), 500):
            open_ = set(chunk)
            q = h.Query(from_block=start, to_block=None if end is None else end + 1,
                        transactions=[h.TransactionSelection(to=sorted(open_), status=1)],
                        field_selection=h.FieldSelection(transaction=TX_FIELDS))
            while open_:
                r = await self.client.get(q)
                for tx in r.data.transactions:  # blocks arrive in order: the first value transfer wins
                    to = tx.to.lower() if tx.to else None
                    if to in open_ and int(tx.value or "0x0", 16) > 0:
                        found[to] = Funding(_addr(tx.to), _addr(tx.from_), int(tx.value, 16) / 1e18,
                                            tx.block_number, tx.hash)
                        open_.discard(to)
                stop = r.archive_height if end is None else min(end + 1, r.archive_height)
                if r.next_block >= stop or not open_:
                    break
                # Resolved wallets leave the selection, so busy wallets stop flooding later pages.
                q.from_block = r.next_block
                q.transactions = [h.TransactionSelection(to=sorted(open_), status=1)]
        return found

    def first_fundings(self, wallets: Iterable[str], end: int | None = None, depth: int = 3) -> list[Funding]:
        """First value transfer into each wallet, then into each funder, up to `depth` hops back
        (treasury -> hop -> puppet). Wallets funded only through contract calls are absent."""
        result: dict[str, Funding] = {}
        frontier = {w.lower() for w in wallets}
        for _ in range(depth):
            frontier -= set(result)
            if not frontier:
                break
            found = asyncio.run(self._first_fundings(sorted(frontier), end))
            result |= found
            frontier = {f.funder.lower() for f in found.values()}
        return sorted(result.values(), key=lambda f: f.block)

    async def _sent_counts(self, funders: list[str], cap: int, end: int | None) -> dict[str, int]:
        counts = {f.lower(): 0 for f in funders}
        for chunk in _chunks(sorted(counts), 100):
            q = h.Query(from_block=0, to_block=None if end is None else end + 1,
                        transactions=[h.TransactionSelection(from_=chunk)],
                        field_selection=h.FieldSelection(transaction=TX_FIELDS))
            open_ = set(chunk)
            while open_:
                r = await self.client.get(q)
                for tx in r.data.transactions:
                    f = tx.from_.lower()
                    if f in open_ and int(tx.value or "0x0", 16) > 0:
                        counts[f] += 1
                        if counts[f] >= cap:
                            open_.discard(f)
                stop = r.archive_height if end is None else min(end + 1, r.archive_height)
                if r.next_block >= stop:
                    break
                q.from_block = r.next_block
                q.transactions = [h.TransactionSelection(from_=sorted(open_))] if open_ else q.transactions
        return counts

    def value_transfers_sent(self, funders: Iterable[str], cap: int = 200, end: int | None = None) -> dict[str, int]:
        """How many value transfers each funder has sent over the chain's history, counted up to `cap`."""
        funders = list({f.lower() for f in funders})
        return asyncio.run(self._sent_counts(funders, cap, end)) if funders else {}

    def height(self) -> int:
        return asyncio.run(self.client.get_height())


def _addr(v) -> str:
    from eth_utils import to_checksum_address

    return to_checksum_address(v)


def main() -> None:
    import time

    from dotenv import load_dotenv

    from .config import REPO_ROOT

    load_dotenv(REPO_ROOT / ".env")
    p = argparse.ArgumentParser()
    p.add_argument("--net", choices=["monad", "monad-testnet"], default="monad")
    args = p.parse_args()
    src = HyperSyncSource(MONAD_MAINNET if args.net == "monad" else MONAD_TESTNET)
    t0 = time.time()
    regs, fb = src.registrations(), src.feedback()
    print(f"{args.net}: {len(regs)} agents, {len(fb)} feedback ({sum(f.revoked for f in fb)} revoked) "
          f"in {time.time() - t0:.0f}s", flush=True)
    wallets = {f.client for f in fb} | {r.owner for r in regs}
    t1 = time.time()
    fund = src.first_fundings(wallets)
    direct = {f.wallet.lower() for f in fund} & {w.lower() for w in wallets}
    print(f"{len(wallets)} wallets: {len(direct)} with a direct funder ({len(direct) / len(wallets):.0%}), "
          f"{len(fund)} funding records incl. funders' funders, in {time.time() - t1:.0f}s", flush=True)


if __name__ == "__main__":
    main()
