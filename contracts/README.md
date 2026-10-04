# contracts

`AgentTrustOracle` stores sybil-adjusted trust scores posted by the offchain model.
The ERC-8004 Identity and Reputation registries are not deployed by us: we use the canonical ones already live on Monad (addresses in `src/interfaces/IERC8004.sol`).

```bash
forge build
forge test                                   # unit + Monad testnet fork tests
forge test --match-contract AgentTrustOracle # unit only, no network
forge script script/DeployOracle.s.sol --rpc-url monad_testnet --account monad-deployer --broadcast
```

After changing a contract's interface, re-export ABIs for the Python side:

```bash
for c in IIdentityRegistry IReputationRegistry AgentTrustOracle; do forge inspect $c abi --json > ../offchain/trustscore/abi/$c.json; done
```
