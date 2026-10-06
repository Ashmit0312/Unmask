import { monadTestnet } from "viem/chains";

export const chain = monadTestnet;
export const RPC_URL = process.env.NEXT_PUBLIC_MONAD_RPC_URL ?? "https://testnet-rpc.monad.xyz";
export const EXPLORER = "https://testnet.monadscan.com";

export const ADDRESSES = {
  oracle: (process.env.NEXT_PUBLIC_ORACLE_ADDRESS ?? "0xdcDe5e5C1315dd6B956d56d6223E47e5401003DA") as `0x${string}`,
  passkeyReviews: (process.env.NEXT_PUBLIC_PASSKEY_REVIEWS_ADDRESS ??
    "0xE5AA2c70C60D55fa0b8dF51DcE53B69e094b6a2E") as `0x${string}`,
  identity: "0x8004A818BFB912233c491871b3d84c89A494BD9e" as `0x${string}`,
  reputation: "0x8004B663056A597Dffe9eCcC1965A193B7388713" as `0x${string}`,
};

/** The thresholds the app shows for isTrusted: score >= 60.00, confidence >= 0.6000 (both in basis points). */
export const GATE = { score: 6000, confidence: 6000 } as const;

export const oracleAbi = [
  {
    type: "function", name: "getScore", stateMutability: "view",
    inputs: [{ name: "agentId", type: "uint256" }],
    outputs: [{
      type: "tuple", components: [
        { name: "score", type: "uint16" }, { name: "confidence", type: "uint16" }, { name: "clusterId", type: "uint32" },
        { name: "epoch", type: "uint64" }, { name: "updatedAt", type: "uint64" },
      ],
    }],
  },
  {
    type: "function", name: "isTrusted", stateMutability: "view",
    inputs: [{ name: "agentId", type: "uint256" }, { name: "minScore", type: "uint16" }, { name: "minConfidence", type: "uint16" }],
    outputs: [{ type: "bool" }],
  },
  { type: "function", name: "epoch", stateMutability: "view", inputs: [], outputs: [{ type: "uint64" }] },
] as const;

export const identityAbi = [
  { type: "function", name: "ownerOf", stateMutability: "view", inputs: [{ name: "agentId", type: "uint256" }], outputs: [{ type: "address" }] },
  { type: "function", name: "tokenURI", stateMutability: "view", inputs: [{ name: "agentId", type: "uint256" }], outputs: [{ type: "string" }] },
] as const;

export const reputationAbi = [
  { type: "function", name: "getClients", stateMutability: "view", inputs: [{ name: "agentId", type: "uint256" }], outputs: [{ type: "address[]" }] },
  {
    type: "function", name: "getSummary", stateMutability: "view",
    inputs: [{ name: "agentId", type: "uint256" }, { name: "clientAddresses", type: "address[]" }, { name: "tag1", type: "string" }, { name: "tag2", type: "string" }],
    outputs: [{ name: "count", type: "uint64" }, { name: "summaryValue", type: "int128" }, { name: "summaryValueDecimals", type: "uint8" }],
  },
] as const;

const webAuthnAuth = {
  name: "auth", type: "tuple", components: [
    { name: "authenticatorData", type: "bytes" }, { name: "clientDataJSON", type: "string" },
    { name: "challengeIndex", type: "uint256" }, { name: "typeIndex", type: "uint256" },
    { name: "r", type: "bytes32" }, { name: "s", type: "bytes32" },
  ],
} as const;

export const passkeyReviewsAbi = [
  {
    type: "function", name: "challengeFor", stateMutability: "view",
    inputs: [{ name: "agentId", type: "uint256" }, { name: "value", type: "uint8" }, { name: "tag", type: "bytes32" }, { name: "x", type: "bytes32" }, { name: "y", type: "bytes32" }],
    outputs: [{ type: "bytes" }],
  },
  {
    type: "function", name: "submit", stateMutability: "nonpayable",
    inputs: [{ name: "agentId", type: "uint256" }, { name: "value", type: "uint8" }, { name: "tag", type: "bytes32" }, { name: "x", type: "bytes32" }, { name: "y", type: "bytes32" }, webAuthnAuth],
    outputs: [],
  },
  {
    type: "function", name: "getReview", stateMutability: "view",
    inputs: [{ name: "reviewer", type: "bytes32" }, { name: "agentId", type: "uint256" }],
    outputs: [{ type: "tuple", components: [{ name: "value", type: "uint8" }, { name: "tag", type: "bytes32" }, { name: "version", type: "uint32" }, { name: "updatedAt", type: "uint64" }] }],
  },
  { type: "error", name: "InvalidPasskeySignature", inputs: [] },
  { type: "error", name: "ValueOutOfRange", inputs: [] },
] as const;
