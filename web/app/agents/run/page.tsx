"use client";
import { Suspense, useCallback, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Button } from "@/components/Button";
import { PageHeader } from "@/components/Eyebrow";
import { IterationReport } from "@/components/IterationReport";
import { RunTimeline } from "@/components/RunTimeline";
import { api } from "@/lib/api";
import { fmtUsd } from "@/lib/format";
import type { AgentRun } from "@/lib/types";
import { useData } from "@/lib/useData";

function RunView() {
  const id = useSearchParams().get("id") ?? "";
  const run = useData(() => api<AgentRun>(`/api/agent/runs/${id}`), `run-${id}`, 3000);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const reload = run.reload;
  const onStatus = useCallback(() => reload(), [reload]);
  const r = run.data;
  if (!r) return <p className="text-muted-48">{run.error ?? "Loading…"}</p>;
  const waiting = r.status === "awaiting_feedback";

  async function respond(action: string) {
    setBusy(true); setMsg(null);
    try { await api(`/api/agent/runs/${id}/respond`, { method: "POST", json: { action, feedback } }); setFeedback(""); reload(); }
    catch (e) { setMsg(String(e instanceof Error ? e.message : e)); } finally { setBusy(false); }
  }
  async function start() {
    setBusy(true); setMsg(null);
    try { await api(`/api/agent/runs/${id}/start`, { method: "POST" }); reload(); }
    catch (e) { setMsg(String(e instanceof Error ? e.message : e)); } finally { setBusy(false); }
  }
  async function raise() {
    setBusy(true);
    try { await api(`/api/agent/runs/${id}/budget`, { method: "POST", json: { max_cost_usd: r!.budget.max_cost_usd * 2 } }); reload(); }
    finally { setBusy(false); }
  }

  return (
    <section>
      <PageHeader eyebrow="Agent run" title={`Iteration ${r.iteration || 0}`} sub="The agent builds, reports, and waits for you." />
      <div className="mb-6 flex flex-wrap items-center gap-4 font-mono text-[12px] text-muted-80">
        <span className="badge badge-cache" data-testid="run-status">{r.status.replace("_", " ")}</span>
        <span data-testid="run-cost">{fmtUsd(r.cost_so_far_usd, 4)} of {fmtUsd(r.budget.max_cost_usd, 2)} budget</span>
        <span>{r.iteration} / {r.budget.max_iterations} iterations</span>
        {r.not_fully_deterministic && <span className="text-warn">not fully deterministic (uses an integration)</span>}
      </div>
      <div className="grid gap-4 md:grid-cols-[1fr_340px]">
        <div className="flex flex-col gap-4">
          {[...r.iterations].reverse().map((it) => <IterationReport key={it.n} it={it} />)}
          {r.status === "awaiting_intake" && (
            <div className="card" data-testid="awaiting-start">
              <p className="mb-3">The intake is saved. Nothing runs until Start{r.intake.integration_ids.length ? " — and an admin has approved its integration" : ""}.</p>
              <Button disabled={busy} onClick={() => void start()} data-testid="start-run">Start</Button>
            </div>
          )}
          {r.cap_reached && (
            <div className="card" data-testid="cap-reached"><p className="mb-3">Paused: {r.cap_reached}</p>
              <Button small onClick={() => void raise()} disabled={busy}>Double the budget</Button></div>
          )}
          {waiting && !r.cap_reached && (
            <div className="card" data-testid="response-bar">
              <textarea rows={2} value={feedback} onChange={(e) => setFeedback(e.target.value)} data-testid="feedback"
                placeholder="Feedback for the next iteration" className="mb-3 w-full rounded-sm border border-hairline px-3 py-2 text-[14px]" />
              <div className="flex flex-wrap gap-3">
                <Button disabled={busy} onClick={() => void respond("accept")} data-testid="accept">Accept</Button>
                <Button variant="ghost" disabled={busy} onClick={() => void respond("improve")} data-testid="improve">Keep improving</Button>
                <Button variant="danger" disabled={busy} onClick={() => void respond("stop")} data-testid="stop">Stop</Button>
              </div>
            </div>
          )}
          {r.status === "accepted" && <p className="card text-ok" data-testid="accepted">Verification passed. An admin can now approve and deploy it from Patterns.</p>}
          {msg && <p className="text-bad">{msg}</p>}
        </div>
        <div className="card"><p className="eyebrow mb-3">Timeline</p><RunTimeline runId={id} initial={r.events} onStatus={onStatus} /></div>
      </div>
    </section>
  );
}

export default function RunPage() {
  return <Suspense><RunView /></Suspense>;
}
