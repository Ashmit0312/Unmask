import Link from "next/link";
import { AgentSearch } from "@/components/AgentSearch";
import { ADDRESSES, EXPLORER } from "@/lib/config";
import { loadSnapshot } from "@/lib/evidence";

const SNIPPET = `interface IAgentTrustOracle {
    function isTrusted(uint256 agentId, uint16 minScore, uint16 minConfidence)
        external view returns (bool);
}

// Before paying or delegating to an ERC-8004 agent:
require(
    IAgentTrustOracle(${ADDRESSES.oracle}).isTrusted(agentId, 6000, 6000),
    "agent not trusted"
);`;

export default async function Home() {
  const main = await loadSnapshot("monad");
  const shady = main.agents["182"];
  const s = main.summary as Record<string, number>;
  const pct = Math.round((100 * (s["agents_with_ring_risk>=0.5"] ?? 0)) / (s.rated_agents || 1));

  return (
    <div className="space-y-16">
      <section className="space-y-6">
        <h1 className="max-w-3xl text-4xl font-semibold tracking-tight sm:text-5xl">
          Trust scores for AI agents that a ring of fake wallets can&apos;t move.
        </h1>
        <p className="max-w-2xl text-lg text-[var(--muted)]">
          ERC-8004 gives every agent an identity and a public review log, and leaves &ldquo;whose reviews count?&rdquo;
          to you. Unmask resolves reviewers into operators, discounts self-dealing and coordinated rings, and
          publishes the result onchain where any contract can check it in one call.
        </p>
        <AgentSearch />
        <p className="text-sm text-[var(--muted)]">
          Try <Link className="underline" href="/agent/1944">1944</Link> (independently reviewed),{" "}
          <Link className="underline" href="/agent/1765">1765</Link> (rated by its own operator) or{" "}
          <Link className="underline" href="/agent/1914">1914</Link> (rates itself).
        </p>
      </section>

      <section className="grid gap-4 sm:grid-cols-3">
        <Stat big={shady ? shady.ratings.toLocaleString() : "–"} label="ratings for one mainnet agent (182), from wallets funded by its own owner" />
        <Stat big={`${pct}%`} label={`of ${s.rated_agents} rated agents on Monad mainnet show self-dealing or ring patterns`} />
        <Stat big="0" label="independent voices behind agent 182's perfect 100 average" />
        <Link href="/findings" className="text-sm underline sm:col-span-3">Read the mainnet findings →</Link>
      </section>

      <section className="space-y-4">
        <h2 className="text-2xl font-semibold">How it works</h2>
        <ol className="grid gap-4 sm:grid-cols-3">
          <Step n={1} title="Index everything">
            Every ERC-8004 registration and review on Monad, plus who funded each reviewer, pulled with Envio HyperSync.
          </Step>
          <Step n={2} title="Resolve operators">
            Wallets that share a funder or rate in coordinated bursts become one operator. Self-dealing is dropped; a ring
            gets at most one vote, discounted by how coordinated it is.
          </Step>
          <Step n={3} title="Publish onchain">
            Scores, confidence and operator flags land in AgentTrustOracle. Passkey reviews add independent voices
            without wallets or gas.
          </Step>
        </ol>
      </section>

      <section id="integrate" className="space-y-4">
        <h2 className="text-2xl font-semibold">Integrate in one call</h2>
        <p className="max-w-2xl text-[var(--muted)]">
          Unmask is a building block: any contract or agent can gate payments, delegation or listings on it.
        </p>
        <pre className="overflow-x-auto rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4 font-mono text-sm">{SNIPPET}</pre>
        <ul className="space-y-1 text-sm text-[var(--muted)]">
          <li>AgentTrustOracle: <a className="font-mono underline" href={`${EXPLORER}/address/${ADDRESSES.oracle}`} target="_blank">{ADDRESSES.oracle}</a></li>
          <li>PasskeyReviews: <a className="font-mono underline" href={`${EXPLORER}/address/${ADDRESSES.passkeyReviews}`} target="_blank">{ADDRESSES.passkeyReviews}</a></li>
        </ul>
      </section>
    </div>
  );
}

function Stat({ big, label }: { big: string; label: string }) {
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-5">
      <p className="font-mono text-3xl">{big}</p>
      <p className="mt-2 text-sm text-[var(--muted)]">{label}</p>
    </div>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-5">
      <p className="font-mono text-sm text-[var(--accent)]">0{n}</p>
      <p className="mt-1 font-semibold">{title}</p>
      <p className="mt-2 text-sm text-[var(--muted)]">{children}</p>
    </li>
  );
}
