"""Pull ERC-8004 Registered / NewFeedback / FeedbackRevoked events into schema records.

Pages forward in `max_log_span` chunks because the public Monad RPC caps eth_getLogs at ~100 blocks.
Historical backfills over millions of blocks belong to a hosted indexer, not this loop.

    python -m trustscore.indexer --from-block 68145000 --to-block 68145400
"""

import argparse
from collections.abc import Iterator

from web3 import Web3

from .config import MONAD_TESTNET, Network, load_abi
from .schema import AgentRegistered, Feedback


def block_ranges(start: int, end: int, span: int) -> Iterator[tuple[int, int]]:
    """Inclusive [lo, hi] windows covering start..end, each at most `span` blocks wide."""
    lo = start
    while lo <= end:
        hi = min(lo + span - 1, end)
        yield lo, hi
        lo = hi + 1


class Indexer:
    def __init__(self, net: Network = MONAD_TESTNET, w3: Web3 | None = None):
        self.net = net
        self.w3 = w3 or Web3(Web3.HTTPProvider(net.rpc_url))
        self.identity = self.w3.eth.contract(address=net.identity_registry, abi=load_abi("IIdentityRegistry"))
        self.reputation = self.w3.eth.contract(address=net.reputation_registry, abi=load_abi("IReputationRegistry"))

    def _logs(self, event, start: int, end: int):
        for lo, hi in block_ranges(start, end, self.net.max_log_span):
            yield from event.get_logs(from_block=lo, to_block=hi)

    def registrations(self, start: int, end: int) -> list[AgentRegistered]:
        return [
            AgentRegistered(
                agent_id=e.args.agentId,
                owner=e.args.owner,
                agent_uri=e.args.agentURI,
                block=e.blockNumber,
                tx_hash=e.transactionHash.hex(),
            )
            for e in self._logs(self.identity.events.Registered, start, end)
        ]

    def feedback(self, start: int, end: int) -> list[Feedback]:
        revoked = {
            (e.args.agentId, e.args.clientAddress, e.args.feedbackIndex)
            for e in self._logs(self.reputation.events.FeedbackRevoked, start, end)
        }
        return [
            Feedback(
                agent_id=e.args.agentId,
                client=e.args.clientAddress,
                index=e.args.feedbackIndex,
                value=e.args.value / 10**e.args.valueDecimals,
                tag1=e.args.tag1,
                tag2=e.args.tag2,
                block=e.blockNumber,
                tx_hash=e.transactionHash.hex(),
                revoked=(e.args.agentId, e.args.clientAddress, e.args.feedbackIndex) in revoked,
            )
            for e in self._logs(self.reputation.events.NewFeedback, start, end)
        ]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--from-block", type=int)
    p.add_argument("--to-block", type=int)
    args = p.parse_args()

    ix = Indexer()
    end = args.to_block or ix.w3.eth.block_number
    start = args.from_block if args.from_block is not None else end - 1_000
    regs, fbs = ix.registrations(start, end), ix.feedback(start, end)
    print(f"blocks {start}..{end}: {len(regs)} registrations, {len(fbs)} feedback")
    for r in regs[:5]:
        print("  agent", r.agent_id, "owner", r.owner)
    for f in fbs[:5]:
        print("  feedback", f.agent_id, "from", f.client, "value", f.value)


if __name__ == "__main__":
    main()
