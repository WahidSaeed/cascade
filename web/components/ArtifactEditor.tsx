"use client";
import Editor, { DiffEditor, loader, type BeforeMount, type OnMount } from "@monaco-editor/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/Button";
import { HistoryPanel, RetrainCard, UsagePanel, type VersionDetail } from "@/components/ControlPanels";
import { filesFor } from "@/components/PatternExplorer";
import { api } from "@/lib/api";
import { emitPatternsChanged } from "@/lib/bus";
import { fmtInt, fmtPct, fmtUsd } from "@/lib/format";
import type { Pattern, VerifyResult } from "@/lib/types";
import { useData } from "@/lib/useData";

// Monaco (the VS Code editor) is served by the app itself: next.config.js copies it to public/monaco/vs.
loader.config({ paths: { vs: "/monaco/vs" } });

type Drift = { agreement: number | null; samples: number; min_agreement: number;
  recent: { ts: number; prompt: string; artifact: string; llm: string; agreement: number; source: string }[] };
type TryResult = { result: string | null; confidence: number; latency_ms: number; error: string | null; fallback: boolean;
  min_confidence: number };
type RunEvent = { kind: string; text: string; ts?: number };
type Run = { id: string; status: string; events: RunEvent[];
  iterations: { metrics?: { agreement?: number; holdout?: number } | null }[] };

// The training run each pattern's Output panel follows; survives the editor remounting on a new version.
const RUNS = new Map<string, string>();
// the open bottom panel per pattern: a new version (approve, rollback, training) remounts the editor
const PANEL_OF = new Map<string, Panel>();

type Panel = "test" | "output" | "problems" | "history" | "usage";
const PANELS: Panel[] = ["test", "output", "problems", "history", "usage"];

const label = "font-mono text-[11px] uppercase tracking-[0.12em] text-muted-48";
const READ_ONLY = new Set(["model_card.json", "examples.txt"]);
const LANG: Record<string, string> = { "handler.py": "python", "model.py": "python", "model_card.json": "json",
  "examples.txt": "plaintext" };

function original(p: Pattern, f: string): string {
  if (f === "handler.py") return p.handler_code ?? "";
  if (f === "model.py") return p.source_code ?? "";
  if (f === "model_card.json") return JSON.stringify(p.model_card ?? {}, null, 2);
  return (p.examples ?? []).join("\n");
}

const defineTheme: BeforeMount = (monaco) => {
  monaco.editor.defineTheme("cascade", {
    base: "vs-dark", inherit: true,
    rules: [{ token: "comment", foreground: "7a7a7a" }, { token: "string", foreground: "2997ff" }],
    colors: { "editor.background": "#272729", "editor.lineHighlightBackground": "#2a2a2c",
      "editorGutter.background": "#252527", "editorLineNumber.foreground": "#7a7a7a",
      "editorLineNumber.activeForeground": "#cccccc" },
  });
};

// The artifact workspace for one pattern: code in Monaco, Test / Output / Problems below, the inspector on the right.
export function ArtifactEditor({ p, file, onChange }: { p: Pattern; file: string | null; onChange: () => void }) {
  const router = useRouter();
  const files = filesFor(p);
  const active = file && files.includes(file) ? file : files[0];
  const pid = p.id ?? "";
  const [edits, setEdits] = useState<Record<string, string>>({});          // unsaved changes over the server copy
  const text = (f: string) => edits[f] ?? original(p, f);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [panel, setPanelState] = useState<Panel>(PANEL_OF.get(pid) ?? (RUNS.has(pid) ? "output" : "test"));
  const setPanel = (k: Panel) => { PANEL_OF.set(pid, k); setPanelState(k); };
  const [diff, setDiff] = useState<VersionDetail | null>(null);
  const [runId, setRunId] = useState<string | null>(RUNS.get(pid) ?? null);
  const [run, setRun] = useState<Run | null>(null);
  const [tryText, setTryText] = useState(p.examples?.[0] ?? "");
  const [tries, setTries] = useState<{ prompt: string; r: TryResult }[]>([]);
  const drift = useData(() => api<Drift>(`/api/patterns/${p.id}/drift`), `drift-${p.id}-${p.updated_at}`);
  const v = p.verification;
  const dirty = (f: string) => !READ_ONLY.has(f) && f in edits && edits[f] !== original(p, f);
  const revert = (f: string) => setEdits((e) => { const next = { ...e }; delete next[f]; return next; });
  const isModel = p.kind === "ml" || p.kind === "dl";

  const changed = () => { onChange(); emitPatternsChanged(); };
  const follow = (id: string) => { RUNS.set(pid, id); setRunId(id); setRun(null); setPanel("output"); };
  const diffOriginal = (d: VersionDetail, f: string) => f === "handler.py" ? d.handler_code : f === "model.py" ? d.source_code
    : f === "model_card.json" ? JSON.stringify(d.model_card ?? {}, null, 2) : original(p, f);

  async function act(name: string, path: string, init: RequestInit & { json?: unknown } = { method: "POST" }) {
    setBusy(name); setMsg(null);
    try {
      const out = await api<Run & Pattern>(path, init);
      if (name === "train") { RUNS.set(pid, out.id); setRunId(out.id); setRun(out); setPanel("output"); }
      if (name === "save") revert("handler.py");
      changed();
    } catch (e) {
      setMsg(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
    }
  }
  const save = () => act("save", `/api/patterns/${p.id}`, { method: "PUT", json: { handler_code: text("handler.py") } });
  const train = () => act("train", `/api/patterns/${p.id}/train-source`, { method: "POST", json: { code: text("model.py") } });
  const primary = useRef<() => void>(() => {});
  useEffect(() => { primary.current = active === "handler.py" ? save : active === "model.py" ? train : () => {}; });

  // OUTPUT: follow the training run until it reports (or fails), then until it is accepted / discarded
  useEffect(() => {
    if (!runId) return;
    let alive = true;
    let done = false;
    const t = setInterval(() => {
      if (done) return;
      api<Run>(`/api/agent/runs/${runId}`).then((r) => {
        if (!alive) return;
        setRun(r);
        if (["accepted", "stopped", "failed"].includes(r.status)) { done = true; clearInterval(t); changed(); }
      }).catch(() => {});
    }, 1000);
    return () => { alive = false; clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  async function tryIt() {
    setBusy("try"); setMsg(null);
    try {
      const r = await api<TryResult>(`/api/patterns/${p.id}/try`, { method: "POST", json: { prompt: tryText } });
      setTries((xs) => [{ prompt: tryText, r }, ...xs].slice(0, 6));
    } catch (e) { setMsg(String(e instanceof Error ? e.message : e)); } finally { setBusy(null); }
  }

  const onMount: OnMount = (editor, monaco) => {
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => primary.current());
  };

  const problems = [
    ...(p.leak_warnings ?? []).map((w) => ({ sev: "error", text: `Literal data: ${w}` })),
    ...(v?.disagreements ?? []).slice(0, 12).map((d) => ({ sev: "warn", text: `${d.prompt} → ${d.artifact} (LLM: ${d.expected ?? d.llm})` })),
    ...(drift.data?.recent ?? []).filter((d) => d.agreement < 1).slice(0, 6).map((d) => ({ sev: "warn", text: `drift: ${d.artifact} ≠ ${d.llm} · ${d.prompt}` })),
  ];
  const lastIt = run?.iterations?.[run.iterations.length - 1];

  return (
    <div className="flex h-full min-h-0" data-testid="artifact-editor">
      <div className="flex min-w-0 flex-1 flex-col">
        {/* tabs + actions */}
        <div className="flex h-[38px] shrink-0 items-end gap-0 border-b border-hairline bg-divider pl-1">
          {files.map((f) => (
            <button key={f} onClick={() => router.push(`/patterns/?id=${p.id}&file=${f}`)} data-testid="editor-tab"
              className={`flex h-[34px] items-center gap-2 border-x border-t px-3 font-mono text-[12px] ${f === active
                ? "border-hairline bg-tile1 text-white" : "border-transparent text-muted-80 hover:text-ink"}`}>
              {f}{READ_ONLY.has(f) && <span className="text-muted-48">🔒</span>}{dirty(f) && <span className="text-sky">●</span>}
            </button>
          ))}
          <div className="ml-auto flex items-center gap-2 self-center pr-3">
            {active === "handler.py" && dirty("handler.py") && <>
              <Button small disabled={!!busy} onClick={save} data-testid="save-code">{busy === "save" ? "Saving…" : "Save"}</Button>
              <Button small variant="ghost" onClick={() => revert("handler.py")}>Revert</Button>
            </>}
            {active === "model.py" && <>
              <Button small disabled={!!busy || !text("model.py")} onClick={train} data-testid="train-source">
                {busy === "train" ? "Starting…" : "Train"}</Button>
              {dirty("model.py") && <Button small variant="ghost" onClick={() => revert("model.py")}>Revert</Button>}
            </>}
          </div>
        </div>

        {/* the code */}
        {diff && (
          <div className="flex shrink-0 items-center gap-3 border-b border-hairline bg-pearl px-3 py-1.5 font-mono text-[11px]" data-testid="diff-bar">
            <span>v{diff.version} <span className="text-muted-48">({diff.change_note || diff.status})</span> ↔ current v{p.version} · {active}</span>
            <button className="ml-auto text-primary" onClick={() => setDiff(null)} data-testid="diff-close">close diff</button>
          </div>
        )}
        <div className="min-h-0 flex-1 bg-tile1" data-testid="code-view" data-file={active}>
          {diff ? (
            <DiffEditor theme="cascade" beforeMount={defineTheme} language={LANG[active] ?? "plaintext"}
              original={diffOriginal(diff, active)} modified={text(active)}
              originalModelPath={`${p.id}/v${diff.version}/${active}`} modifiedModelPath={`${p.id}/diff-current/${active}`}
              options={{ readOnly: true, renderSideBySide: true, fontFamily: "var(--font-mono), Menlo, monospace",
                fontSize: 13, automaticLayout: true, scrollBeyondLastLine: false }} />
          ) : active === "model.py" && !text("model.py") ? (
            <p className="p-6 font-mono text-[13px] text-body-muted">No trained model yet: the agent writes this file when it trains one.</p>
          ) : (
            <Editor theme="cascade" beforeMount={defineTheme} onMount={onMount} path={`${p.id}/${active}`}
              language={LANG[active] ?? "plaintext"} value={text(active)}
              onChange={(val) => { if (!READ_ONLY.has(active)) setEdits((b) => ({ ...b, [active]: val ?? "" })); }}
              options={{ readOnly: READ_ONLY.has(active), fontFamily: "var(--font-mono), Menlo, monospace", fontSize: 13,
                minimap: { enabled: true }, scrollBeyondLastLine: false, automaticLayout: true, tabSize: 4 }}
              loading={<pre className="p-4 font-mono text-[12px] text-white">{text(active)}</pre>} />
          )}
        </div>

        {/* bottom panel */}
        <div className="flex h-[240px] shrink-0 flex-col border-t border-hairline bg-canvas" data-testid="bottom-panel">
          <div className="flex gap-5 border-b border-divider px-4">
            {PANELS.map((k) => (
              <button key={k} onClick={() => setPanel(k)} data-testid={`panel-${k}`}
                className={`py-2 font-mono text-[11px] uppercase tracking-[0.12em] ${panel === k ? "border-b border-primary text-ink" : "text-muted-48"}`}>
                {k}{k === "problems" && problems.length > 0 && <span className="ml-1 rounded-pill bg-divider px-1.5">{problems.length}</span>}
                {k === "output" && run && ["agent_running"].includes(run.status) && <span className="ml-1 text-primary">●</span>}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
            {panel === "test" && (
              <div>
                <div className="flex gap-2">
                  <input value={tryText} onChange={(e) => setTryText(e.target.value)} data-testid="try-input"
                    onKeyDown={(e) => { if (e.key === "Enter" && tryText) void tryIt(); }}
                    placeholder="A prompt to run through the saved artifact…"
                    className="min-w-0 flex-1 rounded-sm border border-hairline bg-pearl px-3 py-1.5 font-mono text-[12px]" />
                  <Button small disabled={!!busy || !tryText || !p.kind} onClick={() => void tryIt()} data-testid="try-run">Run</Button>
                </div>
                <p className="mt-1 text-[12px] text-muted-48">Runs the saved version out of process, exactly as the router would.</p>
                {tries.map(({ prompt, r }, i) => (
                  <div key={i} className="mt-2 border-t border-divider pt-2 font-mono text-[12px]" data-testid={i === 0 ? "try-result" : undefined}>
                    <p className="truncate text-muted-80">› {prompt}</p>
                    {r.error ? <p className="text-bad">error: {r.error}</p>
                      : r.fallback ? <p className="text-warn">↪ defers to the LLM ({r.result === "__FALLBACK__" ? "not this pattern's input"
                        : `confidence ${r.confidence.toFixed(2)} < ${r.min_confidence}`}) · {r.latency_ms} ms</p>
                      : <p><span className="text-ok">{r.result}</span> · confidence {r.confidence.toFixed(2)} · {r.latency_ms} ms</p>}
                  </div>
                ))}
              </div>
            )}
            {panel === "output" && (
              <div className="font-mono text-[12px]" data-testid="output-log">
                {!run && <p className="text-muted-48">{isModel ? "Edit model.py and press Train (⌘S): the code trains in a sandboxed job, then it is evaluated against the LLM." : "Training output appears here for ml / dl artifacts."}</p>}
                {run && <p className="mb-1 text-muted-48">run <Link className="text-primary" href={`/agents/run/?id=${run.id}`}>{run.id}</Link> · <span data-testid="train-status">{run.status}</span></p>}
                {run?.events.map((e, i) => (
                  <pre key={i} className={`whitespace-pre-wrap ${e.kind === "error" ? "text-bad" : "text-muted-80"}`}>[{e.kind}] {e.text}</pre>
                ))}
                {run?.status === "awaiting_feedback" && (
                  <div className="mt-2 flex items-center gap-3">
                    {lastIt?.metrics?.agreement != null && <span>agreement {fmtPct(lastIt.metrics.agreement)} on {lastIt.metrics.holdout} held-out prompts</span>}
                    <Button small disabled={!!busy} data-testid="train-accept"
                      onClick={() => act("accept", `/api/agent/runs/${run.id}/respond`, { method: "POST", json: { action: "accept" } })}>Accept</Button>
                    <Button small variant="ghost" disabled={!!busy} data-testid="train-discard"
                      onClick={() => act("discard", `/api/agent/runs/${run.id}/respond`, { method: "POST", json: { action: "stop" } })}>Discard</Button>
                  </div>
                )}
              </div>
            )}
            {panel === "history" && (
              <HistoryPanel p={p} onChanged={changed} locked={!!busy}
                onDiff={(d) => { setDiff(d); if (d.kind === "rule" && files.includes("handler.py")) router.push(`/patterns/?id=${p.id}&file=handler.py`); }} />
            )}
            {panel === "usage" && <UsagePanel p={p} />}
            {panel === "problems" && (
              <div className="text-[12px]" data-testid="problems">
                {problems.length === 0 && <p className="text-muted-48">No problems.</p>}
                {problems.map((x, i) => (
                  <p key={i} className={`py-0.5 ${x.sev === "error" ? "text-bad" : "text-warn"}`}>{x.sev === "error" ? "✖" : "⚠"} {x.text}</p>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* inspector */}
      <aside className="w-[340px] shrink-0 overflow-y-auto border-l border-hairline bg-canvas p-5" data-testid="inspector">
        <p className="mb-1 font-mono text-[11px] uppercase tracking-[0.12em] text-primary">{p.kind ?? "pattern"} · {p.task_type ?? "untyped"} · v{p.version}</p>
        <h2 className="text-[22px] leading-tight">{p.name}</h2>
        <p className="mt-1 font-mono text-[12px] text-muted-48">{p.status} {p.deployed_function ? `· ${p.deployed_function}` : ""}</p>
        {p.alert && <p className="mt-3 rounded-md bg-divider p-3 text-[13px] text-warn" data-testid="drawer-alert">⚠ {p.alert}</p>}
        {p.not_fully_deterministic && <p className="mt-3 text-[13px] text-warn">Not fully deterministic: depends on an external system.</p>}

        <div className="my-4 flex flex-wrap gap-2">
          {["recognized", "awaiting_intake", "awaiting_feedback", "stopped"].includes(p.status) && (
            <a href={`/patterns/intake/?id=${p.id}`} className="btn" data-testid="open-intake">Open intake</a>
          )}
          <Button small variant="ghost" disabled={!!busy || !p.kind} data-testid="verify"
            onClick={() => act("verify", `/api/patterns/${p.id}/verify`, { method: "POST", json: { samples: [] } })}>
            {busy === "verify" ? "Verifying…" : "Verify"}
          </Button>
          {p.status !== "approved" && (
            <Button small disabled={!!busy || !v?.passed} data-testid="approve"
              onClick={() => act("approve", `/api/patterns/${p.id}/approve`)}>Approve</Button>
          )}
          {p.status !== "rejected" && (
            <Button small variant="danger" disabled={!!busy} data-testid="reject"
              onClick={() => act("reject", `/api/patterns/${p.id}/reject`)}>Reject</Button>
          )}
        </div>
        {msg && <p className="mb-3 text-[13px] text-bad" data-testid="drawer-error">{msg}</p>}
        {p.kind && <RetrainCard p={p} code={text("model.py")} onStarted={follow} />}

        <div className="mb-5 grid grid-cols-2 gap-3 text-[13px]">
          <div><p className={label}>Tokens / request</p><p className="font-mono">{fmtInt(Math.round(p.avg_input_tokens))} in · {fmtInt(Math.round(p.avg_output_tokens))} out</p></div>
          <div><p className={label}>Projected / month</p><p className="font-mono">{fmtUsd(p.projected_monthly_savings_usd, 2)}</p></div>
          <div><p className={label}>Hits</p><p className="font-mono">{fmtInt(p.hits)}</p></div>
          <div><p className={label}>Measured on</p><p className="font-mono">{fmtInt(p.samples_measured)} samples</p></div>
        </div>

        <div className="mb-5" data-testid="verification">
          <p className={`${label} mb-1`}>Verification</p>
          {v ? <VerifyView v={v} /> : <p className="text-[13px] text-muted-48">Not verified yet.</p>}
        </div>

        {drift.data && drift.data.samples > 0 && (
          <div className="mb-5" data-testid="drift">
            <p className={`${label} mb-1`}>Drift (artifact vs LLM on live traffic)</p>
            <p className="font-mono text-[13px]">
              rolling agreement <span className={(drift.data.agreement ?? 1) < drift.data.min_agreement ? "text-bad" : "text-ok"}>
                {fmtPct(drift.data.agreement ?? 0)}</span> over {drift.data.samples} samples (minimum {fmtPct(drift.data.min_agreement)})
            </p>
          </div>
        )}

        {(p.kb_dependencies?.length ?? 0) > 0 && (
          <div className="mb-5" data-testid="kb-dependencies">
            <p className={`${label} mb-1`}>Knowledge dependencies</p>
            {(p.kb_dependencies ?? []).map((d) => <p key={d.document_id} className="font-mono text-[12px]">{d.title || d.document_id} · v{d.version}</p>)}
          </div>
        )}

        {p.kind && p.kind !== "rule" && (
          <div className="mb-5" data-testid="model-card">
            <p className={`${label} mb-1`}>Model card</p>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-[11px]">
              {Object.entries(p.model_card ?? {}).filter(([k, val]) => val != null && !["confusion", "discovery"].includes(k)).map(([k, val]) => (
                <div key={k} className="contents"><dt className="text-muted-48">{k}</dt>
                  <dd className="break-all">{typeof val === "object" ? JSON.stringify(val) : String(val)}</dd></div>
              ))}
            </dl>
          </div>
        )}
      </aside>
    </div>
  );
}

function VerifyView({ v }: { v: VerifyResult }) {
  return (
    <p className="font-mono text-[13px]">
      <span className={v.passed ? "text-ok" : "text-bad"}>{v.passed ? "passed" : "failed"}</span>
      {" · "}agreement {fmtPct(v.score)} on {fmtInt(v.sample_size)} samples ({v.method})
      {(v.fallbacks ?? 0) > 0 && ` · ${v.fallbacks} deferred`}
      {(v.disagreements ?? []).length > 0 && <span className="text-muted-48"> · {(v.disagreements ?? []).length} disagreements (Problems)</span>}
    </p>
  );
}
