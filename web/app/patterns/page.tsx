"use client";
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ArtifactEditor } from "@/components/ArtifactEditor";
import { PageHeader } from "@/components/Eyebrow";
import { Button } from "@/components/Button";
import { api } from "@/lib/api";
import { emitPatternsChanged, onPatternsChanged } from "@/lib/bus";
import { fmtInt, fmtPct, fmtUsd } from "@/lib/format";
import type { Pattern } from "@/lib/types";
import { useData } from "@/lib/useData";

function Overview() {
  const { data, reload } = useData(() => api<Pattern[]>("/api/patterns"), "patterns");
  const router = useRouter();
  const patterns = data ?? [];
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  async function runDiscovery() {
    setBusy(true); setMsg(null);
    try {
      const found = await api<Pattern[]>("/api/patterns/discover", { method: "POST" });
      setMsg(found.length ? `Recognized: ${found.map((p) => p.name).join(", ")}` : "No new patterns.");
      reload(); emitPatternsChanged();
    } catch (e) { setMsg(String(e instanceof Error ? e.message : e)); } finally { setBusy(false); }
  }
  return (
    <section className="mx-auto max-w-[1100px] px-8 pb-16 pt-9">
      <PageHeader eyebrow="Patterns" title="The artifact library."
        sub="Recurring requests compiled into rules and models — verified against the LLM, approved by a person. Open one in the explorer to edit, test and train it." />
      <div className="mb-5 flex items-center gap-4">
        <Button variant="ghost" small disabled={busy} onClick={() => void runDiscovery()} data-testid="run-discovery">
          {busy ? "Discovering…" : "Run discovery"}
        </Button>
        {msg && <span className="text-[14px] text-muted-80" data-testid="discovery-msg">{msg}</span>}
      </div>
      <table className="w-full rounded-md bg-canvas text-[14px]" data-testid="pattern-table">
        <thead><tr className="text-left font-mono text-[11px] uppercase tracking-[0.1em] text-muted-48">
          {["Pattern", "Kind", "Status", "Score", "Hits", "Projected / mo"].map((h) => <th key={h} className="px-4 py-3 font-normal">{h}</th>)}
        </tr></thead>
        <tbody>
          {patterns.map((p) => (
            <tr key={p.id} onClick={() => router.push(`/patterns/?id=${p.id}`)} className="cursor-pointer border-t border-divider hover:bg-pearl">
              <td className="px-4 py-2.5">{p.name}{p.alert && <span className="ml-2 text-[12px] text-warn">⚠</span>}</td>
              <td className="px-4 font-mono text-[12px]">{p.kind ?? "—"} · {p.task_type ?? "untyped"}</td>
              <td className="px-4 font-mono text-[12px]">{p.status.replace("_", " ")}</td>
              <td className="px-4 font-mono text-[12px]">{p.verification_score == null ? "—" : fmtPct(p.verification_score)}</td>
              <td className="px-4 font-mono text-[12px]">{fmtInt(p.hits)}</td>
              <td className="px-4 font-mono text-[12px]">{(p.projected_monthly_savings_usd ?? 0) > 0 ? fmtUsd(p.projected_monthly_savings_usd, 2) : "—"}</td>
            </tr>
          ))}
          {patterns.length === 0 && <tr><td className="px-4 py-3 text-muted-48" colSpan={6}>No patterns yet.</td></tr>}
        </tbody>
      </table>
    </section>
  );
}

function PatternsView() {
  const params = useSearchParams();
  const id = params.get("id");
  const { data: p, reload } = useData(() => (id ? api<Pattern>(`/api/patterns/${id}`) : Promise.resolve(null)),
    `pattern-${id}`, 5000);
  useEffect(() => onPatternsChanged(reload), [reload]);
  if (!id) return <Overview />;
  if (!p) return <p className="p-8 font-mono text-[13px] text-muted-48">Loading…</p>;
  return <ArtifactEditor key={`${p.id}-${p.version}`} p={p} file={params.get("file")} onChange={reload} />;
}

export default function PatternsPage() {
  return <Suspense><PatternsView /></Suspense>;
}
