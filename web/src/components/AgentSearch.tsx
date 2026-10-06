"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function AgentSearch({ placeholder = "Agent ID, e.g. 1944" }: { placeholder?: string }) {
  const router = useRouter();
  const [id, setId] = useState("");
  return (
    <form
      className="flex w-full max-w-md gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (/^\d+$/.test(id.trim())) router.push(`/agent/${id.trim()}`);
      }}
    >
      <input
        value={id}
        onChange={(e) => setId(e.target.value)}
        inputMode="numeric"
        placeholder={placeholder}
        className="min-w-0 flex-1 rounded-lg border border-[var(--line)] bg-[var(--panel)] px-3 py-2 font-mono outline-none focus:border-[var(--accent)]"
      />
      <button className="btn-primary" type="submit">Check</button>
    </form>
  );
}
