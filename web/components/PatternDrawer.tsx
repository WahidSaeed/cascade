"use client";
import { useState } from "react";
import { Button } from "@/components/Button";
import { api } from "@/lib/api";
import { fmtInt, fmtPct, fmtUsd } from "@/lib/format";
import type { Pattern, VerifyResult } from "@/lib/types";

const label = "font-mono text-[11px] uppercase tracking-[0.12em] text-muted-48";

export function PatternDrawer({ p, onClose, onChange }: { p: Pattern; onClose: () => void; onChange: () => void }) {
  const [code, setCode] = useState(p.handler_code);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const v = p.verification;

  async function act(name: string, path: string, init: RequestInit & { json?: unknown } = { method: "POST" }) {
    setBusy(name); setMsg(null);
    try {
      await api(path, init);
      onChange();
      if (name === "save") setEditing(false);
    } catch (e) {
      setMsg(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={onClose}>
      <aside className="shadow-product h-full w-full max-w-[640px] overflow-y-auto bg-canvas p-8" onClick={(e) => e.stopPropagation()}
        data-testid="pattern-drawer">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <p className="eyebrow mb-2">{p.kind ?? "pattern"} · {p.task_type ?? "untyped"} · v{p.version}</p>
            <h2 className="text-[28px] leading-tight">{p.name}</h2>
            <p className="mt-1 font-mono text-[12px] text-muted-48">{p.status} {p.deployed_function ? `· ${p.deployed_function}` : ""}</p>
          </div>
          <button onClick={onClose} className="text-2xl text-muted-48" aria-label="Close">×</button>
        </div>

        {p.alert && <p className="mb-4 rounded-md bg-divider p-3 text-[14px] text-warn" data-testid="drawer-alert">⚠ {p.alert}</p>}
        {p.not_fully_deterministic && <p className="mb-4 text-[14px] text-warn">Not fully deterministic: depends on an external system.</p>}

        <div className="mb-6 grid grid-cols-3 gap-3">
          <div><p className={label}>Avg LLM tokens</p><p className="font-mono">{fmtInt(Math.round(p.avg_input_tokens))} in / {fmtInt(Math.round(p.avg_output_tokens))} out</p></div>
          <div><p className={label}>Measured on</p><p className="font-mono">{fmtInt(p.samples_measured)} samples</p></div>
          <div><p className={label}>Projected / month</p><p className="font-mono">{fmtUsd(p.projected_monthly_savings_usd, 2)}</p></div>
        </div>

        {p.kind === "rule" || !p.kind ? (
          <div className="mb-6">
            <div className="mb-2 flex items-center justify-between"><p className={label}>Code</p>
              {!editing && <button className="text-[13px] text-primary" onClick={() => setEditing(true)} data-testid="edit-code">Edit</button>}
            </div>
            {editing ? (
              <textarea value={code} onChange={(e) => setCode(e.target.value)} rows={18} data-testid="code-editor"
                className="w-full rounded-md border border-hairline bg-pearl p-3 font-mono text-[12px]" />
            ) : (
              <pre className="max-h-[360px] overflow-auto rounded-md bg-tile1 p-4 font-mono text-[12px] leading-relaxed text-white"
                data-testid="code-view">{p.handler_code}</pre>
            )}
          </div>
        ) : (
          <div className="mb-6" data-testid="model-card">
            <p className={`${label} mb-2`}>Model card</p>
            <dl className="grid grid-cols-2 gap-2 font-mono text-[12px]">
              {Object.entries(p.model_card ?? {}).map(([k, val]) => (
                <div key={k} className="contents"><dt className="text-muted-48">{k}</dt><dd>{typeof val === "object" ? JSON.stringify(val) : String(val)}</dd></div>
              ))}
            </dl>
          </div>
        )}

        {(p.kb_dependencies?.length ?? 0) > 0 && (
          <div className="mb-6" data-testid="kb-dependencies">
            <p className={`${label} mb-2`}>Knowledge dependencies</p>
            {(p.kb_dependencies ?? []).map((d) => <p key={d.document_id} className="font-mono text-[12px]">{d.title || d.document_id} · v{d.version}</p>)}
          </div>
        )}

        <div className="mb-6" data-testid="verification">
          <p className={`${label} mb-2`}>Verification</p>
          {v ? <VerifyView v={v} /> : <p className="text-[14px] text-muted-48">Not verified yet.</p>}
        </div>

        {(p.leak_warnings?.length ?? 0) > 0 && (
          <div className="mb-6" data-testid="leak-warnings">
            <p className={`${label} mb-2 !text-bad`}>Literal-data warnings</p>
            {(p.leak_warnings ?? []).map((w) => <p key={w} className="text-[13px] text-bad">{w}</p>)}
          </div>
        )}

        {msg && <p className="mb-4 text-[14px] text-bad" data-testid="drawer-error">{msg}</p>}
        <div className="flex flex-wrap gap-3">
          {editing ? (
            <>
              <Button disabled={!!busy} onClick={() => act("save", `/api/patterns/${p.id}`, { method: "PUT", json: { handler_code: code } })}>Save</Button>
              <Button variant="ghost" onClick={() => { setEditing(false); setCode(p.handler_code); }}>Cancel</Button>
            </>
          ) : (
            <>
              <Button variant="ghost" disabled={!!busy} data-testid="verify"
                onClick={() => act("verify", `/api/patterns/${p.id}/verify`, { method: "POST", json: { samples: [] } })}>
                {busy === "verify" ? "Verifying…" : "Verify"}
              </Button>
              {p.status !== "approved" && (
                <Button disabled={!!busy || !v?.passed} data-testid="approve"
                  onClick={() => act("approve", `/api/patterns/${p.id}/approve`)}>Approve</Button>
              )}
              {p.status !== "rejected" && (
                <Button variant="danger" disabled={!!busy} data-testid="reject"
                  onClick={() => act("reject", `/api/patterns/${p.id}/reject`)}>Reject</Button>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

function VerifyView({ v }: { v: VerifyResult }) {
  return (
    <div>
      <p className="font-mono text-[14px]">
        <span className={v.passed ? "text-ok" : "text-bad"}>{v.passed ? "passed" : "failed"}</span>
        {" · "}agreement {fmtPct(v.score)} on {fmtInt(v.sample_size)} samples ({v.method})
        {(v.fallbacks ?? 0) > 0 && ` · ${v.fallbacks} deferred`}
      </p>
      {(v.disagreements ?? []).length > 0 && (
        <table className="mt-3 w-full text-[12px]" data-testid="disagreements">
          <thead><tr className="text-left text-muted-48"><th className="pr-2 font-normal">Prompt</th><th className="pr-2 font-normal">Artifact</th><th className="font-normal">LLM / label</th></tr></thead>
          <tbody>
            {(v.disagreements ?? []).slice(0, 8).map((d, i) => (
              <tr key={i} className="border-t border-divider align-top">
                <td className="py-1.5 pr-2">{d.prompt}</td>
                <td className="py-1.5 pr-2 font-mono">{d.artifact}</td>
                <td className="py-1.5 font-mono">{d.expected ?? d.llm}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
