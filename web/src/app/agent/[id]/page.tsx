import Link from "next/link";
import { notFound } from "next/navigation";
import { AgentLive } from "@/components/AgentLive";
import { ReviewPanel } from "@/components/ReviewPanel";
import { loadSnapshot, verdict, type AgentEvidence } from "@/lib/evidence";

export default async function AgentPage({ params }: PageProps<"/agent/[id]">) {
  const { id } = await params;
  if (!/^\d{1,12}$/.test(id)) notFound();
  const snap = await loadSnapshot("monad-testnet");
  const e = snap.agents[id];

  return (
    <div className="space-y-8">
      <div>
        <Link href="/" className="text-sm text-[var(--muted)] hover:text-[var(--fg)]">← Unmask</Link>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">Agent {id}</h1>
        <p className="text-sm text-[var(--muted)]">ERC-8004 identity on Monad testnet</p>
      </div>

      <AgentLive agentId={id} />

      {e ? <Evidence e={e} head={snap.head} /> : (
        <p className="text-sm text-[var(--muted)]">No ratings yet in the last indexed snapshot (block {snap.head}).</p>
      )}

      <ReviewPanel agentId={id} />
    </div>
  );
}

function Evidence({ e, head }: { e: AgentEvidence; head: number }) {
  const why = verdict(e);
  return (
    <section className="space-y-4 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-5">
      <h2 className="text-lg font-semibold">Why this score</h2>
      {why && <p className="rounded-lg bg-[var(--bg)] p-3 text-sm"><span className="font-medium text-[var(--bad)]">Discounted:</span> {why}</p>}
      <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
        <Fact label="Ratings" value={e.ratings} />
        <Fact label="Distinct raters" value={e.raters} />
        <Fact label="Independent voices" value={e.independent} />
        <Fact label="Passkey reviews" value={e.passkeyReviews} />
        <Fact label="From own operator" value={`${Math.round(e.selfDealing * 100)}%`} />
        <Fact label="From largest ring" value={`${Math.round(e.ringShare * 100)}%`} />
        <Fact label="Plain average" value={e.naive} />
        <Fact label="Model score" value={e.score} />
      </dl>
      {e.sources.length > 0 && (
        <div>
          <p className="mb-2 text-xs uppercase tracking-wider text-[var(--muted)]">Where the ratings came from (resolved operators)</p>
          <table className="w-full text-left text-sm">
            <thead className="text-[var(--muted)]">
              <tr><th className="py-1 font-normal">Operator</th><th className="font-normal">Wallets</th><th className="font-normal">Ratings</th><th className="font-normal">Avg</th><th className="font-normal">Coordination</th></tr>
            </thead>
            <tbody className="font-mono">
              {e.sources.map((s, i) => (
                <tr key={i} className="border-t border-[var(--line)]">
                  <td className="py-1 font-sans">{s.owner ? <span className="text-[var(--bad)]">agent&apos;s own operator</span> : `operator ${i + 1}`}</td>
                  <td>{s.wallets}</td><td>{s.ratings}</td><td>{s.mean}</td><td>{Math.round(s.suspicion * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-[var(--muted)]">Evidence from the offchain pipeline at block {head}; live scores above are read from the chain.</p>
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <dt className="text-[var(--muted)]">{label}</dt>
      <dd className="font-mono text-lg">{value}</dd>
    </div>
  );
}
