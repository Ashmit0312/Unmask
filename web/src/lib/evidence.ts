import { readFile } from "node:fs/promises";
import path from "node:path";

/** Evidence exported by the offchain pipeline (python -m trustscore.export_web). */
export type Source = { wallets: number; ratings: number; mean: number; suspicion: number; owner: boolean };
export type AgentEvidence = {
  owner: string; ratings: number; raters: number; independent: number; naive: number; score: number;
  confidence: number; selfDealing: number; ringShare: number; risk: number; passkeyReviews: number; sources: Source[];
};
export type Snapshot = {
  network: "monad" | "monad-testnet";
  head: number;
  summary: Record<string, number | string>;
  agents: Record<string, AgentEvidence>;
};

const cache = new Map<string, Snapshot>();

export async function loadSnapshot(net: Snapshot["network"]): Promise<Snapshot> {
  if (!cache.has(net)) {
    const file = path.join(process.cwd(), "public", "data", `${net}.json`);
    cache.set(net, JSON.parse(await readFile(file, "utf8")));
  }
  return cache.get(net)!;
}

/** Plain-language reason the oracle disagrees with ERC-8004's average, or null when they agree. */
export function verdict(e: AgentEvidence): string | null {
  if (e.selfDealing >= 0.5)
    return `${Math.round(e.selfDealing * 100)}% of its ratings come from wallets controlled by its own operator.`;
  if (e.ringShare >= 0.5)
    return `${Math.round(e.ringShare * 100)}% of its ratings come from one coordinated group of wallets.`;
  if (e.ratings >= 5 && e.independent < 2)
    return `${e.ratings} ratings, but only ${e.independent} independent ${e.independent === 1 ? "voice" : "voices"} behind them.`;
  return null;
}
