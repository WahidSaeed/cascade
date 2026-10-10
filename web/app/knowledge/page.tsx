"use client";
import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/Button";
import { PageHeader } from "@/components/Eyebrow";
import { UploadDropzone, uploadFile } from "@/components/UploadDropzone";
import { api } from "@/lib/api";
import { fmtInt, fmtPct } from "@/lib/format";
import type { ChunkPreview, Collection, Doc, RagMetrics } from "@/lib/types";
import { useData } from "@/lib/useData";
import { useUser } from "@/lib/user";

const BADGE: Record<string, string> = { org: "everyone", group: "group", private: "private" };

function KnowledgeView() {
  const { userId } = useUser();
  const params = useSearchParams();
  const router = useRouter();
  const cols = useData(() => api<Collection[]>("/api/collections"), `cols-${userId}`, 5000);
  const metrics = useData(() => api<RagMetrics>("/api/rag/metrics"), "rag", 10000);
  const selected = params.get("c") ?? cols.data?.[0]?.id ?? null;
  const docs = useData(() => (selected ? api<Doc[]>(`/api/documents?collection_id=${selected}`) : Promise.resolve([])),
    `docs-${selected}-${userId}`, 2000);
  const [preview, setPreview] = useState<{ doc: Doc; chunks: ChunkPreview[] } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const m = metrics.data;

  async function act(path: string, method: string) {
    setMsg(null);
    try { await api(path, { method }); docs.reload(); } catch (e) { setMsg(String(e instanceof Error ? e.message : e)); }
  }

  return (
    <section>
      <PageHeader eyebrow="Knowledge" title="Answers grounded in your documents."
        sub="Access-filtered retrieval, cited answers, and caches that follow every document change." />
      {m && (
        <div className="mb-6 flex flex-wrap gap-x-6 gap-y-1 font-mono text-[12px] text-muted-80" data-testid="rag-metrics">
          <span>knowledge requests {fmtInt(m.knowledge_requests)}</span>
          <span>cache hit rate {fmtPct(m.knowledge_cache_hit_rate)}</span>
          <span>citation rate {fmtPct(m.citation_rate)}</span>
          <span>avg context {fmtInt(Math.round(m.avg_context_tokens))} tokens</span>
          <span>no-context rate {fmtPct(m.relevance_gate_rate)}</span>
          <span>retrieval p95 {m.retrieval_latency_ms.p95} ms</span>
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-[320px_1fr]">
        <div className="flex flex-col gap-3" data-testid="collections">
          {(cols.data ?? []).map((c) => (
            <button key={c.id} onClick={() => router.push(`/knowledge/?c=${c.id}`)} data-testid={`collection-${c.id}`}
              className={`card !p-4 text-left ${c.id === selected ? "border-primary" : ""}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[17px] font-semibold">{c.name}</span>
                <span className="badge badge-cache" data-testid="access-badge">{BADGE[c.access]}{c.access === "group" ? `: ${c.group_ids.join(", ")}` : ""}</span>
              </div>
              <div className="mt-2 font-mono text-[11px] text-muted-48">
                {c.documents} docs · {fmtInt(c.tokens)} tokens indexed
                {c.usage && <> · {fmtInt(c.usage.requests)} questions · {fmtInt(c.usage.cache_hits)} cached · {fmtInt(c.usage.context_tokens_avoided)} context tokens avoided</>}
              </div>
            </button>
          ))}
        </div>
        <div className="flex flex-col gap-4">
          {selected && <UploadDropzone collectionId={selected} onUploaded={docs.reload} />}
          {msg && <p className="text-[14px] text-muted-80" data-testid="knowledge-msg">{msg}</p>}
          <div className="card overflow-x-auto">
            <table className="w-full text-[14px]" data-testid="documents">
              <thead><tr className="text-left font-mono text-[11px] uppercase tracking-[0.1em] text-muted-48">
                <th className="py-2 font-normal">Document</th><th className="font-normal">Version</th><th className="font-normal">Status</th><th /></tr></thead>
              <tbody>
                {(docs.data ?? []).map((d) => (
                  <tr key={d.id} className="border-t border-divider" data-testid="document-row" data-title={d.title}>
                    <td className="py-2.5">{d.title}<div className="font-mono text-[11px] text-muted-48">{d.filename}</div></td>
                    <td className="font-mono" data-testid="doc-version">v{d.version}{d.pending_version ? ` → v${d.pending_version}` : ""}</td>
                    <td className="font-mono text-[12px]" data-testid="doc-status">
                      <span className={d.status === "ready" ? "text-ok" : d.status === "failed" ? "text-bad" : "text-muted-80"}>{d.status}</span>
                      {d.pending_status && <span className="text-muted-48"> · v{d.pending_version} {d.pending_status}</span>}
                      {d.error && <div className="text-bad">{d.error}</div>}
                    </td>
                    <td className="space-x-3 whitespace-nowrap text-right text-[13px]">
                      <button className="text-primary" data-testid="preview" onClick={async () =>
                        setPreview({ doc: d, chunks: await api<ChunkPreview[]>(`/api/documents/${d.id}/chunks`) })}>chunks</button>
                      <label className="cursor-pointer text-primary" data-testid="new-version">new version
                        <input type="file" className="hidden" data-testid="new-version-input" onChange={async (e) => {
                          const f = e.target.files?.[0];
                          e.target.value = "";
                          if (!f) return;
                          try { await uploadFile(d.collection_id, f, d.title); setMsg(`${d.title}: new version queued`); docs.reload(); }
                          catch (err) { setMsg(String(err instanceof Error ? err.message : err)); }
                        }} />
                      </label>
                      <button className="text-primary" onClick={() => void act(`/api/documents/${d.id}/reindex`, "POST")}>re-index</button>
                      <button className="text-bad" onClick={() => void act(`/api/documents/${d.id}`, "DELETE")}>delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {preview && (
            <div className="card" data-testid="chunk-preview">
              <div className="mb-3 flex items-center justify-between"><p className="eyebrow !mb-0">{preview.doc.title} · v{preview.doc.version}</p>
                <Button variant="ghost" small onClick={() => setPreview(null)}>Close</Button></div>
              {preview.chunks.map((c) => (
                <div key={c.id} className="mb-3 border-t border-divider pt-3">
                  <p className="font-mono text-[11px] text-muted-48">#{c.ord} · {c.heading_path}{c.page ? ` · p. ${c.page}` : ""} · {c.token_estimate} tokens</p>
                  <p className="whitespace-pre-wrap text-[14px]">{c.text}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

export default function KnowledgePage() {
  return <Suspense><KnowledgeView /></Suspense>;
}
