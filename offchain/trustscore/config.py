import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
ABI_DIR = Path(__file__).parent / "abi"

load_dotenv(REPO_ROOT / ".env")


@dataclass(frozen=True)
class Network:
    name: str
    chain_id: int
    rpc_url: str
    identity_registry: str
    reputation_registry: str
    # Public Monad RPC rejects eth_getLogs spans wider than this.
    max_log_span: int = 100


MONAD_TESTNET = Network(
    name="monad_testnet",
    chain_id=10143,
    rpc_url=os.getenv("MONAD_RPC_URL", "https://testnet-rpc.monad.xyz"),
    identity_registry="0x8004A818BFB912233c491871b3d84c89A494BD9e",
    reputation_registry="0x8004B663056A597Dffe9eCcC1965A193B7388713",
    # Public RPC: 100. QuickNode Build: 1000. Set MONAD_LOG_SPAN to match whatever MONAD_RPC_URL points at.
    max_log_span=int(os.getenv("MONAD_LOG_SPAN", "100")),
)

MONAD_MAINNET = Network(
    name="monad_mainnet",
    chain_id=143,
    rpc_url=os.getenv("MONAD_MAINNET_RPC_URL", "https://rpc.monad.xyz"),
    identity_registry="0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
    reputation_registry="0x8004BAa17C55a88189AE136b182e5fdA19dE9b63",
)


def load_abi(name: str) -> list:
    return json.loads((ABI_DIR / f"{name}.json").read_text())
