"use client";
import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { PageHeader } from "@/components/Eyebrow";
import { PatternCard } from "@/components/PatternCard";
import { PatternDrawer } from "@/components/PatternDrawer";
import { Button } from "@/components/Button";
import { useState } from "react";
import { api } from "@/lib/api";
import type { Pattern } from "@/lib/types";
import { useData } from "@/lib/useData";

function PatternsView() {
  const { data, reload } = useData(() => api<Pattern[]>("/api/patterns"), "patterns");
  const patterns = data ?? [];
  const params = useSearchParams();
  const router = useRouter();
  const openId = params.get("id");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  async function runDiscovery() {
    setBusy(true); setMsg(null);
    try {
      const found = await api<Pattern[]>("/api/patterns/discover", { method: "POST" });
      setMsg(found.length ? `Recognized: ${found.map((p) => p.name).join(", ")}` : "No new patterns.");
      reload();
    } catch (e) { setMsg(String(e instanceof Error ? e.message : e)); } finally { setBusy(false); }
  }

  const open = patterns.find((p) => p.id === openId);

  return (
    <section>
      <PageHeader eyebrow="Patterns" title="The artifact library."
        sub="Recurring requests compiled into rules and models — verified against the LLM, approved by a person." />
      <div className="mb-5 flex items-center gap-4">
        <Button variant="ghost" small disabled={busy} onClick={() => void runDiscovery()} data-testid="run-discovery">
          {busy ? "Discovering…" : "Run discovery"}
        </Button>
        {msg && <span className="text-[14px] text-muted-80" data-testid="discovery-msg">{msg}</span>}
      </div>
      <div className="grid gap-4 md:grid-cols-3" data-testid="pattern-grid">
        {patterns.map((p) => <PatternCard key={p.id} p={p} onOpen={() => router.push(`/patterns/?id=${p.id}`)} />)}
        {patterns.length === 0 && <p className="text-muted-48">No patterns yet.</p>}
      </div>
      {open && <PatternDrawer key={`${open.id}-${open.version}-${open.updated_at}`} p={open}
        onClose={() => router.push("/patterns/")} onChange={reload} />}
    </section>
  );
}

export default function PatternsPage() {
  return <Suspense><PatternsView /></Suspense>;
}
