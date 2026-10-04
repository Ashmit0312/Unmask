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
.venv/Scripts/python -m trustscore.evaluate --seeds 5   # baselines vs model across 5 attack scenarios (~10 s)

# replay a small simulated world through the real registries (local fork: free, ~6 min for 235 txs)
anvil --fork-url https://testnet-rpc.monad.xyz
.venv/Scripts/python -m trustscore.replay --rpc-url http://127.0.0.1:8545 --fund anvil --out ../data/replay-local
# same on live testnet, funded from the Foundry keystore (~8.5 MON at the 100 gwei floor)
.venv/Scripts/python -m trustscore.replay --fund keystore:monad-deployer --out ../data/replay-testnet
```

Secrets: copy `.env.example` to `.env`. The deployer key goes in Foundry's encrypted keystore, not `.env`:
`cast wallet import monad-deployer --interactive`.

## End-to-end pipeline (local fork)

Index from chain → score → post changed scores to `AgentTrustOracle` → read back and compare to ground truth.
Anvil dev keys below are public and only valid locally.

```bash
# deploy the oracle (anvil account 0 deploys, account 1 is the updater)
cd contracts
ORACLE_UPDATER=0x70997970C51812dc3A010C7d01b50e0d17dc79C8 forge script script/DeployOracle.s.sol \
  --rpc-url http://127.0.0.1:8545 --broadcast \
  --private-key 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
rm -rf broadcast/DeployOracle.s.sol/10143   # a fork shares testnet's chain id; keep real deploy records clean

# score and post; --watch 5 keeps indexing new blocks and posting changes
cd ../offchain
.venv/Scripts/python -m trustscore.oracle --rpc-url http://127.0.0.1:8545 --oracle <oracle address> \
  --key 0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d \
  --labels ../data/replay-local --out ../data/replay-local/scores.csv
```
