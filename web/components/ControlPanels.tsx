"use client";
import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/Button";
import { API_BASE, api } from "@/lib/api";
import { fmtInt, fmtPct, fmtUsd } from "@/lib/format";
import type { Dataset, Integration, Pattern } from "@/lib/types";
import { useData } from "@/lib/useData";
import { currentUserId } from "@/lib/user";

// The admin control centre for one artifact: version history (diff, rollback), usage across users, retraining.

export type VersionRow = { version: number; kind: string | null; status: string; current: boolean; change_note: string;
  approved_by: string | null; verification_score: number | null; verification_passed: boolean; created_at: number;
  updated_at: number };
export type VersionDetail = { version: number; kind: string | null; status: string; change_note: string;
  handler_code: string; source_code: string; model_card: Record<string, unknown> };
type UserUsage = { user_id: string; name: string; artifact_requests: number; llm_requests: number; other_requests: number;
  tokens_avoided: number; cost_avoided_usd: number; cost_usd: number; last_bucket: number };
type Usage = { users: number; users_served_by_artifact: number; artifact_requests: number; llm_requests: number;
  tokens_avoided: number; cost_avoided_usd: number; per_user: UserUsage[] };

// the last control-centre message per pattern (survives the editor remounting on the new version it creates)
const NOTES = new Map<string, string>();

const th = "px-2 py-1 text-left font-mono text-[10px] font-normal uppercase tracking-[0.1em] text-muted-48";
const when = (ts: number) => new Date(ts * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" });

export function HistoryPanel({ p, onDiff, onChanged, locked = false }: {
  p: Pattern; onDiff: (v: VersionDetail) => void; onChanged: () => void; locked?: boolean;
}) {
  const { data, reload } = useData(() => api<VersionRow[]>(`/api/patterns/${p.id}/versions`), `versions-${p.id}-${p.version}-${p.updated_at}`);
  const [confirm, setConfirm] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const pid = p.id ?? "";
  const [msg, setMsgState] = useState<string | null>(NOTES.get(pid) ?? null);
  const setMsg = (m: string | null) => { if (m) NOTES.set(pid, m); else NOTES.delete(pid); setMsgState(m); };

  async function diff(v: number) {
    setMsg(null);
    try { onDiff(await api<VersionDetail>(`/api/patterns/${p.id}/versions/${v}`)); }
    catch (e) { setMsg(String(e instanceof Error ? e.message : e)); }
  }
  async function rollback(v: number) {
    setBusy(true); setMsg(null);
    try {
      const r = await api<{ version: number; status: string; pending: string | null }>(
        `/api/patterns/${p.id}/rollback`, { method: "POST", json: { version: v } });
      setMsg(r.pending ? `Restored as v${r.version} (draft): ${r.pending}` : `Restored v${v} as v${r.version} — approved and serving.`);
      setConfirm(null); reload(); onChanged();
    } catch (e) { setMsg(String(e instanceof Error ? e.message : e)); } finally { setBusy(false); }
  }

  const rows = data ?? [];
  return (
    <div data-testid="history">
      {msg && <p className="mb-2 text-[12px] text-muted-80" data-testid="history-msg">{msg}</p>}
      {rows.length === 0 && <p className="text-[12px] text-muted-48">No artifact versions yet.</p>}
      {rows.length > 0 && (
        <table className="w-full text-[12px]">
          <thead><tr>{["Version", "Status", "What changed", "Verification", "When", ""].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
          <tbody>
            {rows.map((v) => (
              <tr key={v.version} className="border-t border-divider align-middle" data-testid="version-row" data-version={v.version}>
                <td className="px-2 py-1.5 font-mono">v{v.version}{v.current && <span className="ml-1 text-primary">● current</span>}</td>
                <td className="px-2 font-mono">{v.status}{v.approved_by ? ` · ${v.approved_by}` : ""}</td>
                <td className="px-2">{v.change_note || "—"}</td>
                <td className="px-2 font-mono">{v.verification_score == null ? "—"
                  : <span className={v.verification_passed ? "text-ok" : "text-bad"}>{fmtPct(v.verification_score)}</span>}</td>
                <td className="px-2 font-mono text-muted-80">{when(v.created_at)}</td>
                <td className="whitespace-nowrap px-2 text-right">
                  {!v.current && <>
                    <button className="mr-3 font-mono text-[11px] text-primary" onClick={() => void diff(v.version)}
                      data-testid={`diff-v${v.version}`}>diff</button>
                    {confirm === v.version ? (
                      <Button small variant="danger" disabled={busy} onClick={() => void rollback(v.version)}
                        data-testid={`confirm-rollback-v${v.version}`}>{busy ? "Rolling back…" : `Confirm: restore v${v.version}`}</Button>
                    ) : (
                      <button className="font-mono text-[11px] text-warn disabled:text-muted-48" disabled={locked}
                        title={locked ? "wait for the current save / action to finish" : undefined}
                        onClick={() => setConfirm(v.version)} data-testid={`rollback-v${v.version}`}>roll back</button>
                    )}
                  </>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-2 text-[11px] text-muted-48">Rollback restores that version as a new one (history is never rewritten) and redeploys it if it had passed verification. Admins only.</p>
    </div>
  );
}

export function UsagePanel({ p }: { p: Pattern }) {
  const { data } = useData(() => api<Usage>(`/api/patterns/${p.id}/usage`), `usage-${p.id}-${p.updated_at}`, 10000);
  if (!data) return <p className="text-[12px] text-muted-48">Loading…</p>;
  const tile = (label: string, value: string, id: string) => (
    <div className="rounded-sm bg-pearl px-3 py-2"><p className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted-48">{label}</p>
      <p className="font-mono text-[15px]" data-testid={id}>{value}</p></div>
  );
  return (
    <div data-testid="usage">
      <div className="mb-3 grid grid-cols-5 gap-2">
        {tile("Users served", `${data.users_served_by_artifact} of ${data.users}`, "usage-users")}
        {tile("By the artifact", fmtInt(data.artifact_requests), "usage-artifact")}
        {tile("Still on the LLM", fmtInt(data.llm_requests), "usage-llm")}
        {tile("Tokens avoided", fmtInt(data.tokens_avoided), "usage-tokens")}
        {tile("Saved", fmtUsd(data.cost_avoided_usd, 4), "usage-saved")}
      </div>
      <table className="w-full text-[12px]">
        <thead><tr>{["User", "Artifact", "LLM", "Tokens avoided", "Saved", "Last used"].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
        <tbody>
          {data.per_user.map((u) => (
            <tr key={u.user_id} className="border-t border-divider" data-testid="usage-row">
              <td className="px-2 py-1">{u.name}</td>
              <td className="px-2 font-mono">{fmtInt(u.artifact_requests)}</td>
              <td className="px-2 font-mono">{fmtInt(u.llm_requests)}</td>
              <td className="px-2 font-mono">{fmtInt(u.tokens_avoided)}</td>
              <td className="px-2 font-mono">{fmtUsd(u.cost_avoided_usd, 4)}</td>
              <td className="px-2 font-mono text-muted-80">{u.last_bucket ? when(u.last_bucket) : "—"}</td>
            </tr>
          ))}
          {data.per_user.length === 0 && <tr><td className="px-2 py-2 text-muted-48" colSpan={6}>No requests matched this pattern yet.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

/** Retrain an ml / dl artifact on more data (and attach an approved integration); for a rule, rebuild with the agent. */
export function RetrainCard({ p, code, onStarted }: { p: Pattern; code: string; onStarted: (runId: string) => void }) {
  const isModel = p.kind === "ml" || p.kind === "dl";
  const [file, setFile] = useState<File | null>(null);
  const [cols, setCols] = useState<string[]>([]);
  const [map, setMap] = useState({ input: "", label: "" });
  const [added, setAdded] = useState<Dataset[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [rulesRun, setRulesRun] = useState<string | null>(null);
  const integ = useData(() => api<Integration[]>("/api/integrations?status=approved"), "integrations-approved");
  const sources = (integ.data ?? []).filter((i) => i.role === "feature_source");

  async function pick(f: File | null) {
    setFile(f); setCols([]);
    if (!f) return;
    const head = (await f.text()).split(/\r?\n/)[0];
    const c = f.name.endsWith(".jsonl") ? Object.keys(JSON.parse(head)) : head.split(",").map((x) => x.trim());
    setCols(c);
    setMap({ input: c.find((x) => /text|input|prompt/i.test(x)) ?? c[0], label: c.find((x) => /label|class/i.test(x)) ?? "" });
  }
  async function upload() {
    if (!file) return;
    setBusy(true); setMsg(null);
    try {
      const up = await api<{ url: string; key: string }>("/api/datasets/upload-url", { method: "POST", json: { filename: file.name } });
      const target = up.url.startsWith("/") ? `${API_BASE}${up.url}` : up.url;
      const put = await fetch(target, { method: "PUT", body: file, headers: { "X-User-Id": currentUserId() } });
      if (!put.ok) throw new Error(`upload failed (${put.status})`);
      const ds = await api<Dataset>("/api/datasets", { method: "POST", json: {
        filename: file.name, key: up.key, use: "train", columns_map: { input: map.input, label: map.label } } });
      setAdded((xs) => [...xs, ds]); setFile(null); setCols([]);
    } catch (e) { setMsg(String(e instanceof Error ? e.message : e)); } finally { setBusy(false); }
  }
  async function start() {
    setBusy(true); setMsg(null);
    const dataset_ids = added.map((d) => d.id);
    try {
      if (isModel) {
        const run = await api<{ id: string }>(`/api/patterns/${p.id}/train-source`, { method: "POST",
          json: { code, dataset_ids, integration_ids: chosen } });
        onStarted(run.id);
      } else {
        const run = await api<{ id: string }>("/api/agent/runs", { method: "POST", json: { pattern_id: p.id, dataset_ids,
          integration_ids: chosen, instructions: "Rebuild this artifact with the additional data.", start: true } });
        setRulesRun(run.id);
      }
    } catch (e) { setMsg(String(e instanceof Error ? e.message : e)); } finally { setBusy(false); }
  }

  const sel = "rounded-sm border border-hairline bg-pearl px-1.5 py-0.5 font-mono text-[11px]";
  return (
    <div className="mb-5 rounded-md border border-hairline p-3" data-testid="retrain">
      <p className="mb-1 font-mono text-[11px] uppercase tracking-[0.12em] text-muted-48">{isModel ? "Retrain with more data" : "Rebuild with more data"}</p>
      <p className="mb-2 text-[12px] text-muted-80">{isModel
        ? "Trains the current model.py on the data it was trained on plus what you add here, then verifies it against the LLM."
        : "Starts the agent on this pattern with the added data; it proposes a new version for review."}</p>
      <input type="file" accept=".csv,.jsonl" data-testid="retrain-file" className="w-full text-[11px]"
        onChange={(e) => void pick(e.target.files?.[0] ?? null)} />
      {cols.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
          <label>input <select className={sel} value={map.input} onChange={(e) => setMap({ ...map, input: e.target.value })}>
            {cols.map((c) => <option key={c}>{c}</option>)}</select></label>
          <label>label <select className={sel} value={map.label} onChange={(e) => setMap({ ...map, label: e.target.value })}>
            {cols.map((c) => <option key={c}>{c}</option>)}</select></label>
          <Button small variant="ghost" disabled={busy || !map.label} onClick={() => void upload()} data-testid="retrain-upload">Add</Button>
        </div>
      )}
      {added.map((d) => <p key={d.id} className="mt-1 font-mono text-[11px] text-ok" data-testid="retrain-dataset">+ {d.rows} rows ({d.pii_status})</p>)}
      {sources.length > 0 && (
        <div className="mt-2 text-[11px]">
          <p className="text-muted-48">Attach an approved integration (feature source):</p>
          {sources.map((i) => (
            <label key={i.id} className="mr-3 inline-flex items-center gap-1">
              <input type="checkbox" checked={chosen.includes(i.id)} data-testid="retrain-integration"
                onChange={(e) => setChosen((xs) => e.target.checked ? [...xs, i.id] : xs.filter((x) => x !== i.id))} />{i.name}
            </label>
          ))}
        </div>
      )}
      <div className="mt-2 flex items-center gap-2">
        <Button small disabled={busy || (added.length === 0 && chosen.length === 0) || (isModel && !code)}
          onClick={() => void start()} data-testid="retrain-start">{isModel ? "Retrain" : "Rebuild with agent"}</Button>
        {rulesRun && <Link className="font-mono text-[11px] text-primary" href={`/agents/run/?id=${rulesRun}`} data-testid="retrain-run">follow the run →</Link>}
      </div>
      {msg && <p className="mt-1 text-[12px] text-bad">{msg}</p>}
    </div>
  );
}
