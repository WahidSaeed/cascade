"use client";
import { useState } from "react";
import { Button } from "@/components/Button";
import { API_BASE, api } from "@/lib/api";
import { fmtInt, fmtUsd } from "@/lib/format";
import type { AgentRun, Dataset, Intake, Integration } from "@/lib/types";
import { currentUserId } from "@/lib/user";

const q = "mb-2 text-[17px] font-semibold";
const hint = "mb-3 text-[13px] text-muted-48";
const input = "w-full rounded-sm border border-hairline bg-canvas px-3 py-2 text-[14px]";

// The agent asks before it spends (rule 9): instructions, data, integrations, budget — then Start.
export function IntakeForm({ intake, onStarted }: { intake: Intake; onStarted: (run: AgentRun) => void }) {
  const [instructions, setInstructions] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [columns, setColumns] = useState<string[]>([]);
  const [map, setMap] = useState({ input: "", label: "" });
  const [use, setUse] = useState("both");
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [integ, setInteg] = useState({ name: "", target: "", role: "feature_source", field: "tier", map: '{"gold": "priority-"}' });
  const [integration, setIntegration] = useState<Integration | null>(null);
  const [budget, setBudget] = useState(intake.budget);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function pick(f: File | null) {
    setFile(f); setDataset(null);
    if (!f) return;
    const head = (await f.text()).split(/\r?\n/)[0];
    const cols = f.name.endsWith(".jsonl") ? Object.keys(JSON.parse(head)) : head.split(",").map((c) => c.trim());
    setColumns(cols);
    setMap({ input: cols.find((c) => /text|input|prompt/i.test(c)) ?? cols[0], label: cols.find((c) => /label|class/i.test(c)) ?? "" });
  }

  async function uploadDataset() {
    if (!file) return;
    setBusy(true); setMsg(null);
    try {
      const up = await api<{ url: string; key: string }>("/api/datasets/upload-url", { method: "POST", json: { filename: file.name } });
      const target = up.url.startsWith("/") ? `${API_BASE}${up.url}` : up.url;
      const put = await fetch(target, { method: "PUT", body: file, headers: { "X-User-Id": currentUserId() } });
      if (!put.ok) throw new Error(`upload failed (${put.status})`);
      setDataset(await api<Dataset>("/api/datasets", { method: "POST", json: {
        filename: file.name, key: up.key, use, columns_map: { input: map.input, ...(map.label ? { label: map.label } : {}) } } }));
    } catch (e) { setMsg(String(e instanceof Error ? e.message : e)); } finally { setBusy(false); }
  }

  async function registerIntegration() {
    setBusy(true); setMsg(null);
    try {
      setIntegration(await api<Integration>("/api/integrations", { method: "POST", json: {
        name: integ.name, type: "http", target: integ.target, role: integ.role,
        config: { method: "GET", param: "email", field: integ.field, map: JSON.parse(integ.map || "{}") } } }));
    } catch (e) { setMsg(String(e instanceof Error ? e.message : e)); } finally { setBusy(false); }
  }

  async function start() {
    setBusy(true); setMsg(null);
    try {
      // the intake is saved as a run first; Start may still wait for an integration's approval (rule 11)
      const run = await api<AgentRun>("/api/agent/runs", { method: "POST", json: {
        pattern_id: intake.pattern.id, instructions, dataset_ids: dataset ? [dataset.id] : [],
        integration_ids: integration ? [integration.id] : [], max_iterations: budget.max_iterations,
        max_cost_usd: budget.max_cost_usd, start: false } });
      await api(`/api/agent/runs/${run.id}/start`, { method: "POST" }).catch(() => undefined);
      onStarted(run);
    } catch (e) { setMsg(String(e instanceof Error ? e.message : e)); setBusy(false); }
  }

  return (
    <div className="flex flex-col gap-4" data-testid="intake-form">
      <div className="card">
        <p className={q}>1 · Additional instructions <span className="text-[13px] font-normal text-muted-48">(optional)</span></p>
        <p className={hint}>Labels to use, edge cases, accuracy vs latency, kinds to try or avoid.</p>
        <textarea rows={3} className={input} value={instructions} onChange={(e) => setInstructions(e.target.value)}
          data-testid="intake-instructions" placeholder="e.g. Use these six classes; refund is separate from returns" />
      </div>
      <div className="card">
        <p className={q}>2 · New dataset <span className="text-[13px] font-normal text-muted-48">(optional)</span></p>
        <p className={hint}>CSV or JSONL. Map the columns and choose how it is used. It is validated, PII-scanned and de-duplicated.</p>
        <input type="file" accept=".csv,.jsonl" data-testid="dataset-file" onChange={(e) => void pick(e.target.files?.[0] ?? null)} />
        {columns.length > 0 && (
          <div className="mt-3 grid grid-cols-3 gap-3 text-[13px]">
            <label>input <select className={input} value={map.input} data-testid="map-input"
              onChange={(e) => setMap({ ...map, input: e.target.value })}>{columns.map((c) => <option key={c}>{c}</option>)}</select></label>
            <label>label <select className={input} value={map.label} data-testid="map-label"
              onChange={(e) => setMap({ ...map, label: e.target.value })}><option value="">(none)</option>{columns.map((c) => <option key={c}>{c}</option>)}</select></label>
            <label>use <select className={input} value={use} data-testid="dataset-use" onChange={(e) => setUse(e.target.value)}>
              <option value="train">train</option><option value="eval">evaluate</option><option value="both">both</option></select></label>
          </div>
        )}
        {file && !dataset && <Button small variant="ghost" className="mt-3" disabled={busy} onClick={() => void uploadDataset()} data-testid="upload-dataset">Upload &amp; validate</Button>}
        {dataset && (
          <p className="mt-3 font-mono text-[12px] text-muted-80" data-testid="dataset-report">
            ✓ {fmtInt(dataset.rows)} rows · labels {Object.entries(dataset.report.label_distribution).map(([k, v]) => `${k} ${v}`).join(", ")} ·
            PII {dataset.pii_status}{dataset.report.pii_rows ? ` (${dataset.report.pii_rows} rows)` : ""} · {dataset.report.duplicates_of_logged_prompts} already in logs
          </p>
        )}
      </div>
      <div className="card">
        <p className={q}>3 · Integrations with existing systems <span className="text-[13px] font-normal text-muted-48">(optional)</span></p>
        <p className={hint}>An HTTP endpoint the artifact may call. It needs an admin&apos;s approval before anything can call it.</p>
        {!integration ? (
          <div className="grid grid-cols-2 gap-3 text-[13px]">
            <input className={input} placeholder="Name (e.g. CRM tier lookup)" value={integ.name} data-testid="integration-name"
              onChange={(e) => setInteg({ ...integ, name: e.target.value })} />
            <input className={input} placeholder="https://…/customers/tier" value={integ.target} data-testid="integration-target"
              onChange={(e) => setInteg({ ...integ, target: e.target.value })} />
            <select className={input} value={integ.role} onChange={(e) => setInteg({ ...integ, role: e.target.value })}>
              <option value="feature_source">feature source</option><option value="pre_post">pre/post-processing</option>
              <option value="core_model">core model</option></select>
            <input className={input} placeholder='map, e.g. {"gold": "priority-"}' value={integ.map}
              onChange={(e) => setInteg({ ...integ, map: e.target.value })} />
            <Button small variant="ghost" disabled={busy || !integ.name || !integ.target} onClick={() => void registerIntegration()}
              data-testid="register-integration">Register</Button>
          </div>
        ) : (
          <p className="font-mono text-[12px]" data-testid="integration-status">{integration.name} · {integration.status}</p>
        )}
      </div>
      <div className="card">
        <p className={q}>Budget</p>
        <div className="flex flex-wrap items-center gap-4 text-[14px]">
          <label>max iterations <input type="number" className={`${input} !w-20`} value={budget.max_iterations}
            onChange={(e) => setBudget({ ...budget, max_iterations: Number(e.target.value) })} /></label>
          <label>max cost (USD) <input type="number" step="0.5" className={`${input} !w-24`} value={budget.max_cost_usd}
            data-testid="budget-usd" onChange={(e) => setBudget({ ...budget, max_cost_usd: Number(e.target.value) })} /></label>
          <span className="font-mono text-[12px] text-muted-80" data-testid="payback">
            first iteration ≈ {fmtUsd(intake.first_iteration_estimate_usd, 4)} · projected savings {fmtUsd(intake.projected_monthly_savings_usd, 2)}/month
            {intake.payback_days !== null && ` · the full budget pays back in ~${intake.payback_days} days`}
          </span>
        </div>
      </div>
      {msg && <p className="text-[14px] text-bad" data-testid="intake-error">{msg}</p>}
      <div><Button disabled={busy} onClick={() => void start()} data-testid="start-run">Start</Button></div>
    </div>
  );
}
