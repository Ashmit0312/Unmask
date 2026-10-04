"""Records shared by the onchain indexer and the simulator, so the model never knows which produced them."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentRegistered:
    agent_id: int
    owner: str
    agent_uri: str
    block: int
    tx_hash: str


@dataclass(frozen=True)
class Feedback:
    agent_id: int
    client: str
    index: int
    value: float  # value / 10**decimals, already normalised
    tag1: str
    tag2: str
    block: int
    tx_hash: str
    revoked: bool = False


@dataclass(frozen=True)
class Funding:
    """First native-token transfer into a wallet: who bankrolled it."""

    wallet: str
    funder: str
    amount: float  # MON
    block: int
    tx_hash: str
