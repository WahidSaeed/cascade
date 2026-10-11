"use client";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/Button";
import { PageHeader } from "@/components/Eyebrow";
import { api, ApiError } from "@/lib/api";
import { SCENARIOS, type DemoAction, type Scenario } from "@/lib/demoScript";
import { useUser } from "@/lib/user";

type DemoState = { demo: boolean; seed: { seed_version?: string; requests?: number } | null; latency_ms: number;
  crm_tier_url?: string;
  replay?: { running: boolean; done: number; total: number } };
type StepResult = { ok: boolean; text: string };

function summarize(v: unknown): string {
  if (Array.isArray(v)) return v.length ? v.map((x) => summarize(x)).join(" · ") : "nothing new";
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    if (typeof o.message === "string") return o.message;
    return Object.entries(o).filter(([, x]) => typeof x !== "object" || Array.isArray(x))
      .map(([k, x]) => `${k}: ${Array.isArray(x) ? x.join(", ") : String(x)}`).join(" · ");
  }
  return String(v);
}

function ScenarioCard({ s, results, busy, run }: { s: Scenario; results: Record<string, StepResult>;
  busy: string | null; run: (a: DemoAction) => void }) {
  const { userId, setUserId } = useUser();
  return (
    <div className="card" data-testid="scenario-card" data-scenario={s.id}>
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <p className="eyebrow">{s.id} · {s.minutes} min{s.spare ? " · spare" : ""}</p>
        <Button small variant={userId === s.persona ? "dark" : "ghost"} data-testid={`persona-${s.id}`}
          onClick={() => setUserId(s.persona)}>
          {userId === s.persona ? `As ${s.persona}` : `Switch to ${s.persona}`}
        </Button>
      </div>
      <h3 className="mb-1 text-[21px] font-semibold tracking-[-0.02em]">{s.title}</h3>
      {s.personaNote && <p className="mb-3 text-[13px] text-muted-48">Persona: {s.persona}, {s.personaNote}</p>}
      <ol className="mb-3 list-decimal space-y-1 pl-5 font-mono text-[12px] text-muted-80">
        {s.prompts.map((p) => <li key={p}>{p}</li>)}
      </ol>
      <ul className="mb-3 list-disc space-y-1 pl-5 text-[14px]" data-testid="talking-points">
        {s.points.map((p) => <li key={p}>{p}</li>)}
      </ul>
      {s.actions?.map((a) => (
        <div key={a.id} className="mt-3 border-t border-divider pt-3">
          <Button small onClick={() => run(a)} disabled={busy !== null} data-testid={`run-${a.id}`} title={a.hint}>
            {busy === a.id ? "Running…" : `Run step: ${a.label}`}
          </Button>
          <p className="mt-1 text-[12px] text-muted-48">{a.hint}</p>
          {results[a.id] && (
            <p className={`mt-1 font-mono text-[12px] ${results[a.id].ok ? "text-ink" : "text-bad"}`}
              data-testid={`result-${a.id}`}>{results[a.id].text}</p>
          )}
        </div>
      ))}
      <p className="mt-3 text-[12px] text-muted-48">Fallback: demo/backup/{s.id}-*.webm</p>
    </div>
  );
}

export default function DemoPage() {
  const [state, setState] = useState<DemoState | null>(null);
  const [off, setOff] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, StepResult>>({});
  const [resetMsg, setResetMsg] = useState<string | null>(null);

  const load = useCallback(() => {
    api<DemoState>("/api/demo/state").then(setState).catch((e) => {
      if (e instanceof ApiError && e.status === 404) setOff(true);
    });
  }, []);
  useEffect(load, [load]);
  useEffect(() => {                         // follow a running traffic replay
    if (!state?.replay?.running) return;
    const t = setTimeout(load, 1000);
    return () => clearTimeout(t);
  }, [state, load]);

  async function run(a: DemoAction) {
    setBusy(a.id);
    try {
      const out = await api<unknown>(a.path, { method: "POST" });
      setResults((r) => ({ ...r, [a.id]: { ok: true, text: summarize(out) } }));
    } catch (e) {
      setResults((r) => ({ ...r, [a.id]: { ok: false, text: e instanceof Error ? e.message : String(e) } }));
    } finally {
      setBusy(null);
      load();
    }
  }

  async function reset() {
    setBusy("reset"); setResetMsg("Restoring the starting state…");
    try {
      await api("/api/demo/reset", { method: "POST" });
      setResults({}); setResetMsg("Back to the starting state.");
    } catch (e) {
      setResetMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null); load();
    }
  }

  if (off) {
    return (
      <section data-testid="demo-off">
        <PageHeader eyebrow="Demo" title="Demo mode is off." sub="Start the API with CASCADE_DEMO=true (make demo-up) to use the run-of-show." />
      </section>
    );
  }
  const main = SCENARIOS.filter((s) => !s.spare);
  const spares = SCENARIOS.filter((s) => s.spare);
  return (
    <section>
      <PageHeader eyebrow="Demo" title="Run of show." sub="About 12 minutes: S1 → S2 → S7 → S4 → S5 → S6 → S3. Script: demo/SCRIPT.md." />
      <div className="card mb-6 flex flex-wrap items-center gap-4" data-testid="demo-state">
        <span className="font-mono text-[12px] text-muted-80">
          Demo mode · seed {state?.seed?.seed_version ?? "…"} · LLM latency {state?.latency_ms ?? "…"} ms
          {state?.crm_tier_url && <> · CRM <span data-testid="crm-url">{state.crm_tier_url}</span></>}
          {state?.replay && state.replay.total > 0 &&
            ` · replay ${state.replay.done}/${state.replay.total}${state.replay.running ? "…" : " done"}`}
        </span>
        <Button small variant="danger" className="ml-auto" onClick={reset} disabled={busy !== null} data-testid="demo-reset">
          {busy === "reset" ? "Resetting…" : "Reset"}
        </Button>
        {resetMsg && <span className="w-full font-mono text-[12px]" data-testid="demo-reset-status">{resetMsg}</span>}
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {main.map((s) => <ScenarioCard key={s.id} s={s} results={results} busy={busy} run={run} />)}
      </div>
      <p className="eyebrow mb-4 mt-10">Spares</p>
      <div className="grid gap-4 md:grid-cols-2">
        {spares.map((s) => <ScenarioCard key={s.id} s={s} results={results} busy={busy} run={run} />)}
      </div>
    </section>
  );
}
