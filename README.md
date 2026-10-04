# Monad agent trust layer

Metropolis hackathon, Track 04. Sybil-resistant reputation for ERC-8004 AI agents on Monad: read public feedback,
find agents and raters likely run by one operator, discount collusive ratings, publish a trust score onchain.

ERC-8004's `getSummary` refuses to aggregate unless the caller supplies the list of raters to trust. Choosing that
list well is the problem we solve.

## Layout

| Path | What | Language |
| --- | --- | --- |
| `contracts/` | `AgentTrustOracle` + interfaces to the live ERC-8004 registries | Solidity / Foundry |
| `offchain/` | Event indexer, agent simulator, scoring model, score poster | Python |
| `web/` | Passkey (Mera) frontend and live demo | TypeScript (later) |

## Network

| | Monad testnet |
| --- | --- |
| Chain id | 10143 |
| RPC | https://testnet-rpc.monad.xyz (eth_getLogs capped at ~100 blocks) |
| Faucet | https://testnet.monad.xyz |
| ERC-8004 Identity | `0x8004A818BFB912233c491871b3d84c89A494BD9e` |
| ERC-8004 Reputation | `0x8004B663056A597Dffe9eCcC1965A193B7388713` |

## Setup

```bash
# contracts (Foundry 1.8.4)
git submodule update --init
cd contracts && forge test

# offchain (Python 3.11)
cd offchain && python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest
.venv/Scripts/python -m trustscore.indexer   # last 1000 testnet blocks of ERC-8004 events
.venv/Scripts/python -m trustscore.simulate --seed 0 --out ../data/sim0   # synthetic world + ground-truth labels
```

Secrets: copy `.env.example` to `.env`. The deployer key goes in Foundry's encrypted keystore, not `.env`:
`cast wallet import monad-deployer --interactive`.
