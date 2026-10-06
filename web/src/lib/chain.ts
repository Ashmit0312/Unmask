import { createPublicClient, http } from "viem";
import { ADDRESSES, GATE, RPC_URL, chain, identityAbi, oracleAbi, reputationAbi } from "./config";

export const publicClient = createPublicClient({ chain, transport: http(RPC_URL) });

export type LiveAgent = {
  exists: boolean;
  owner?: `0x${string}`;
  uri?: string;
  /** What ERC-8004 reports by default: getSummary over every rater it knows. */
  erc8004: { count: number; average: number | null };
  oracle: { scored: boolean; score: number; confidence: number; clusterId: number; epoch: number; updatedAt: number };
  trusted: boolean;
};

export async function readAgent(agentId: bigint): Promise<LiveAgent> {
  const owner = await publicClient
    .readContract({ address: ADDRESSES.identity, abi: identityAbi, functionName: "ownerOf", args: [agentId] })
    .catch(() => undefined);
  const empty = { count: 0, average: null };
  if (!owner) {
    return { exists: false, erc8004: empty, oracle: { scored: false, score: 0, confidence: 0, clusterId: 0, epoch: 0, updatedAt: 0 }, trusted: false };
  }
  const [uri, clients, s, trusted] = await Promise.all([
    publicClient.readContract({ address: ADDRESSES.identity, abi: identityAbi, functionName: "tokenURI", args: [agentId] }).catch(() => ""),
    publicClient.readContract({ address: ADDRESSES.reputation, abi: reputationAbi, functionName: "getClients", args: [agentId] }),
    publicClient.readContract({ address: ADDRESSES.oracle, abi: oracleAbi, functionName: "getScore", args: [agentId] }),
    publicClient.readContract({ address: ADDRESSES.oracle, abi: oracleAbi, functionName: "isTrusted", args: [agentId, GATE.score, GATE.confidence] }),
  ]);
  let erc8004: LiveAgent["erc8004"] = empty;
  if (clients.length) {
    const [count, value, decimals] = await publicClient.readContract({
      address: ADDRESSES.reputation, abi: reputationAbi, functionName: "getSummary", args: [agentId, clients, "", ""],
    });
    erc8004 = { count: Number(count), average: count ? Number(value) / 10 ** decimals : null };
  }
  return {
    exists: true, owner, uri,
    erc8004,
    oracle: {
      scored: s.epoch > BigInt(0), score: s.score / 100, confidence: s.confidence / 10_000,
      clusterId: s.clusterId, epoch: Number(s.epoch), updatedAt: Number(s.updatedAt),
    },
    trusted,
  };
}
