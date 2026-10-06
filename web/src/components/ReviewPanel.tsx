"use client";

import { useState, useSyncExternalStore } from "react";
import { stringToHex, type Hex } from "viem";
import { publicClient } from "@/lib/chain";
import { ADDRESSES, EXPLORER, passkeyReviewsAbi } from "@/lib/config";
import { createAccount, forgetPasskey, loadPasskey, signIn, signReview, type Session } from "@/lib/passkey";

type Step = "idle" | "working" | "done" | "error";

/** Review an agent with Face ID: Mera account from the passkey, review signed by the same passkey, relayed gas-free. */
export function ReviewPanel({ agentId }: { agentId: string }) {
  const [session, setSession] = useState<Session | null>(null);
  // Whether this device holds a passkey: read from localStorage on the client, false during server render.
  const [, rerender] = useState(0);
  const hasPasskey = useSyncExternalStore(() => () => {}, () => !!loadPasskey(), () => false);
  const [value, setValue] = useState(80);
  const [step, setStep] = useState<Step>("idle");
  const [msg, setMsg] = useState<string>("");
  const [tx, setTx] = useState<Hex | null>(null);

  const run = async (fn: () => Promise<void>) => {
    setStep("working");
    setMsg("");
    try {
      await fn();
    } catch (e) {
      setStep("error");
      const m = e instanceof Error ? e.message : String(e);
      setMsg(/PRF_UNAVAILABLE/.test(m)
        ? "This passkey provider does not support PRF. Use iCloud Keychain, Google Password Manager or 1Password."
        : m);
    }
  };

  const connect = (create: boolean) =>
    run(async () => {
      const s = create ? await createAccount(`reviewer-${Date.now().toString(36)}`) : await signIn();
      setSession(s);
      rerender((n) => n + 1);
      setStep("idle");
    });

  const submit = () =>
    run(async () => {
      if (!session) return;
      const tag = stringToHex("quality", { size: 32 });
      const { x, y } = session.passkey;
      const challenge = await publicClient.readContract({
        address: ADDRESSES.passkeyReviews, abi: passkeyReviewsAbi, functionName: "challengeFor",
        args: [BigInt(agentId), value, tag, x, y],
      });
      setMsg("Confirm with Face ID / Touch ID…");
      const auth = await signReview(session.passkey, challenge);
      setMsg("Relaying (you pay no gas)…");
      const res = await fetch("/api/relay", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agentId, value, tag, x, y, auth }),
      });
      const j = await res.json();
      if (!res.ok) throw new Error(j.error ?? "Relay failed");
      setTx(j.hash);
      setStep("done");
      setMsg("Verified onchain by Monad's P256 precompile. The oracle picks it up on its next pass.");
    });

  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-5">
      <h2 className="text-lg font-semibold">Review with your passkey</h2>
      <p className="mt-1 text-sm text-[var(--muted)]">
        No wallet, no seed phrase, no gas. Your passkey is your account (via Mera) and signs your review, which Monad
        verifies onchain. One review per passkey per agent; you can revise it.
      </p>

      {!session ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {hasPasskey && (
            <button className="btn-primary" disabled={step === "working"} onClick={() => connect(false)}>Sign in with passkey</button>
          )}
          <button className={hasPasskey ? "btn" : "btn-primary"} disabled={step === "working"} onClick={() => connect(true)}>
            Create a passkey
          </button>
          {hasPasskey && (
            <button className="btn-ghost" onClick={() => (forgetPasskey(), rerender((n) => n + 1))}>Forget this device&apos;s passkey</button>
          )}
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          <p className="text-sm">
            Signed in as <span className="font-mono">{session.address.slice(0, 8)}…{session.address.slice(-6)}</span>
            <span className="text-[var(--muted)]"> (Mera account from your passkey)</span>
          </p>
          <label className="block">
            <span className="text-sm text-[var(--muted)]">Your rating: <span className="font-mono text-[var(--fg)]">{value}</span> / 100</span>
            <input type="range" min={0} max={100} value={value} onChange={(e) => setValue(+e.target.value)} className="mt-2 w-full accent-[var(--accent)]" />
          </label>
          <button className="btn-primary" disabled={step === "working"} onClick={submit}>Sign and submit review</button>
        </div>
      )}

      {msg && <p className={`mt-3 text-sm ${step === "error" ? "text-[var(--bad)]" : "text-[var(--muted)]"}`}>{msg}</p>}
      {tx && (
        <a className="mt-1 block text-sm underline" href={`${EXPLORER}/tx/${tx}`} target="_blank">View transaction</a>
      )}
    </div>
  );
}
