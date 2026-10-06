# web

The Unmask frontend: look up any ERC-8004 agent on Monad testnet, see ERC-8004's default `getSummary` next to the
sybil-resistant trust score from `AgentTrustOracle`, read the evidence behind it, and review it with a passkey.

| Route | What |
| --- | --- |
| `/` | Pitch, mainnet headline numbers, how it works, `isTrusted()` integration snippet |
| `/agent/[id]` | Live onchain score vs ERC-8004 default, evidence (operators, self-dealing, rings), passkey review |
| `/findings` | Monad mainnet analysis (agent 182 and other flagged agents) |
| `/api/relay` | Relays passkey-signed reviews to `PasskeyReviews`, paying gas; rejects invalid signatures in simulation |

Passkeys: Mera derives the account from the passkey's PRF output; the same passkey's P256 key signs reviews, verified
onchain by Monad's P256 precompile. PRF needs iCloud Keychain, Google Password Manager or 1Password (on desktop Chrome,
only passkeys saved to Google Password Manager return PRF).

```bash
npm install
npm run dev     # http://localhost:3000
```

`.env.local` (git-ignored):

```
RELAYER_PRIVATE_KEY=0x...            # pays gas for relayed reviews (testnet only)
# optional overrides
NEXT_PUBLIC_MONAD_RPC_URL=
NEXT_PUBLIC_ORACLE_ADDRESS=
NEXT_PUBLIC_PASSKEY_REVIEWS_ADDRESS=
```

Evidence snapshots in `public/data/` come from `python -m trustscore.export_web --net monad-testnet` (and `--net monad`).
