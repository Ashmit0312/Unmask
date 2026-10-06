import { loadSnapshot, verdict } from "@/lib/evidence";

export const metadata = { title: "Mainnet findings · Unmask" };

export default async function Findings() {
  const snap = await loadSnapshot("monad");
  const s = snap.summary as Record<string, number>;
  const a = snap.agents["182"];
  const flagged = Object.entries(snap.agents)
    .filter(([, e]) => e.risk >= 0.5)
    .sort(([, x], [, y]) => y.ratings - x.ratings)
    .slice(0, 12);

  return (
    <div className="space-y-10">
      <section className="space-y-4">
        <h1 className="text-3xl font-semibold tracking-tight">What ERC-8004 on Monad mainnet looks like</h1>
        <p className="max-w-2xl text-[var(--muted)]">
          Every registration and review on Monad mainnet to block {snap.head.toLocaleString()}, indexed with Envio
          HyperSync, with each reviewer traced back to the wallet that funded it. Analysis only: the oracle is deployed
          on testnet.
        </p>
        <dl className="grid gap-4 sm:grid-cols-4">
          <Num v={s.agents} l="agents registered" />
          <Num v={s.rated_agents} l="agents with any rating" />
          <Num v={s.ratings} l="ratings" />
          <Num v={s["agents_with_ring_risk>=0.5"]} l="rated agents with self-dealing or ring patterns" />
        </dl>
      </section>

      {a && (
        <section className="space-y-3 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-6">
          <h2 className="text-xl font-semibold">Agent 182: {a.ratings.toLocaleString()} perfect ratings, zero independent ones</h2>
          <p className="text-[var(--muted)]">
            ERC-8004 stops an owner from rating its own agent. Agent 182&apos;s owner funded {a.raters.toLocaleString()} fresh
            wallets instead, and each rated the agent once. That is {Math.round((100 * a.ratings) / s.ratings)}% of every
            rating on Monad mainnet. Its ERC-8004 average is {a.naive}; resolved to operators, every one of those wallets
            is the owner, so the independent evidence is {a.independent} and the trust score is {a.score}.
          </p>
          <p className="font-mono text-sm">owner {a.owner}</p>
        </section>
      )}

      <section className="space-y-3">
        <h2 className="text-xl font-semibold">Most-rated flagged agents</h2>
        <table className="w-full text-left text-sm">
          <thead className="text-[var(--muted)]">
            <tr><th className="py-1 font-normal">Agent</th><th className="font-normal">Ratings</th><th className="font-normal">Independent</th><th className="font-normal">Average</th><th className="font-normal">Score</th><th className="font-normal">Why</th></tr>
          </thead>
          <tbody>
            {flagged.map(([id, e]) => (
              <tr key={id} className="border-t border-[var(--line)] align-top">
                <td className="py-2 font-mono">{id}</td>
                <td className="font-mono">{e.ratings.toLocaleString()}</td>
                <td className="font-mono">{e.independent}</td>
                <td className="font-mono">{e.naive}</td>
                <td className="font-mono">{e.score}</td>
                <td className="text-[var(--muted)]">{verdict(e) ?? "coordinated raters"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-xs text-[var(--muted)]">
          Ratings are normalised to 0–100 per app tag, since ERC-8004 leaves the scale to each app. Funding links come
          from HyperSync; exchanges are separated from operators by how many transfers they have ever sent.
        </p>
      </section>
    </div>
  );
}

function Num({ v, l }: { v: number; l: string }) {
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4">
      <dd className="font-mono text-2xl">{(v ?? 0).toLocaleString()}</dd>
      <dt className="mt-1 text-sm text-[var(--muted)]">{l}</dt>
    </div>
  );
}
