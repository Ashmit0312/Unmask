"use client";

import { useEffect, useState } from "react";
import { readAgent, type LiveAgent } from "@/lib/chain";
import { ADDRESSES, EXPLORER, GATE } from "@/lib/config";

const fmt = (n: number | null, d = 1) => (n === null ? "–" : n.toFixed(d));

/** Live onchain view of one agent: ERC-8004's own average next to the trust oracle. Refreshes every 10 s. */
export function AgentLive({ agentId }: { agentId: string }) {
  const [a, setA] = useState<LiveAgent | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const load = () =>
      readAgent(BigInt(agentId)).then((r) => live && (setA(r), setErr(null))).catch((e) => live && setErr(String(e.message ?? e)));
    load();
    const t = setInterval(load, 10_000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [agentId]);

  if (err) return <p className="text-sm text-[var(--bad)]">Could not read the chain: {err}</p>;
  if (!a) return <div className="h-40 animate-pulse rounded-xl bg-[var(--panel)]" />;
  if (!a.exists) return <p className="text-[var(--muted)]">Agent {agentId} is not registered on Monad testnet.</p>;

  const o = a.oracle;
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-5">
        <p className="text-xs uppercase tracking-wider text-[var(--muted)]">ERC-8004 getSummary (default)</p>
        <p className="mt-2 font-mono text-4xl">{fmt(a.erc8004.average)}</p>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Raw average of {a.erc8004.count} ratings: every wallet counts, and each app rates on its own scale
        </p>
      </div>
      <div className={`rounded-xl border p-5 ${a.trusted ? "border-[var(--good)]" : "border-[var(--line)]"} bg-[var(--panel)]`}>
        <div className="flex items-center justify-between">
          <p className="text-xs uppercase tracking-wider text-[var(--muted)]">Unmask trust score (onchain)</p>
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${a.trusted ? "bg-[var(--good)] text-black" : "bg-[var(--line)] text-[var(--muted)]"}`}>
            {a.trusted ? "isTrusted" : "not trusted"}
          </span>
        </div>
        {o.scored ? (
          <>
            <p className="mt-2 font-mono text-4xl">{fmt(o.score)}</p>
            <p className="mt-1 text-sm text-[var(--muted)]">
              confidence {fmt(o.confidence, 2)} · epoch {o.epoch}
              {o.clusterId ? <span className="ml-2 text-[var(--bad)]">· operator flagged #{o.clusterId}</span> : null}
            </p>
          </>
        ) : (
          <p className="mt-2 text-sm text-[var(--muted)]">Not scored yet: the oracle only scores agents with ratings.</p>
        )}
      </div>
      <p className="text-xs text-[var(--muted)] sm:col-span-2">
        Owner <a className="underline" href={`${EXPLORER}/address/${a.owner}`} target="_blank">{a.owner}</a> · trust gate: score ≥{" "}
        {GATE.score / 100}, confidence ≥ {GATE.confidence / 10_000} ·{" "}
        <a className="underline" href={`${EXPLORER}/address/${ADDRESSES.oracle}`} target="_blank">oracle contract</a>
      </p>
    </div>
  );
}
