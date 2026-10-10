"use client";
import { useEffect, useState } from "react";
import { Button } from "@/components/Button";
import { PageHeader } from "@/components/Eyebrow";
import { api } from "@/lib/api";
import type { ToolConfig } from "@/lib/types";

const GROUPS: [string, string[]][] = [
  ["Models", ["llm", "bedrock_model_id", "embed_backend"]],
  ["Light layer", ["cache_threshold", "pattern_threshold", "pattern_min_prob", "pattern_min_margin", "global_cache"]],
  ["Artifacts", ["artifact_runtime", "artifact_min_confidence", "verify_min_score", "ml_min_samples", "dl_min_samples"]],
  ["Quality over time", ["drift_sample_rate", "drift_min_agreement"]],
  ["Knowledge", ["rag_max_context_tokens", "rag_top_k"]],
  ["Agent", ["agent_max_iterations", "agent_max_cost_usd"]],
  ["Demo", ["demo_latency_ms"]],
];

export default function ToolsPage() {
  const [cfg, setCfg] = useState<ToolConfig | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [prices, setPrices] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    api<ToolConfig>("/api/config").then((c) => {
      setCfg(c); setDraft(c.values); setPrices(JSON.stringify(c.values.model_prices, null, 2));
    }).catch((e) => setMsg(String(e)));
  }, []);

  async function save() {
    if (!cfg) return;
    setMsg(null);
    const changed: Record<string, unknown> = {};
    for (const k of cfg.editable) {
      if (k === "model_prices") continue;
      if (JSON.stringify(draft[k]) !== JSON.stringify(cfg.values[k])) changed[k] = draft[k];
    }
    try {
      const parsed = JSON.parse(prices);
      if (JSON.stringify(parsed) !== JSON.stringify(cfg.values.model_prices)) changed.model_prices = parsed;
    } catch {
      setMsg("model prices: invalid JSON"); return;
    }
    try {
      const c = await api<ToolConfig>("/api/config", { method: "PUT", json: { values: changed } });
      setCfg(c); setDraft(c.values); setMsg(Object.keys(changed).length ? "Saved." : "Nothing changed.");
    } catch (e) {
      setMsg(String(e instanceof Error ? e.message : e));
    }
  }

  if (!cfg) return <p className="text-muted-48">{msg ?? "Loading configuration…"}</p>;
  return (
    <section>
      <PageHeader eyebrow="Tools" title="Configuration." sub="Every backend and threshold, switchable without a deploy." />
      <div className="grid gap-4 md:grid-cols-2">
        {GROUPS.map(([title, keys]) => (
          <div key={title} className="card">
            <p className="eyebrow mb-4">{title}</p>
            {keys.filter((k) => k in draft).map((k) => (
              <label key={k} className="mb-3 flex items-center justify-between gap-4 text-[14px]">
                <span className="font-mono text-[12px] text-muted-80">{k}</span>
                {typeof draft[k] === "boolean" ? (
                  <input type="checkbox" checked={Boolean(draft[k])} data-testid={`cfg-${k}`}
                    onChange={(e) => setDraft({ ...draft, [k]: e.target.checked })} />
                ) : (
                  <input value={String(draft[k] ?? "")} data-testid={`cfg-${k}`}
                    className="w-56 rounded-sm border border-hairline px-2 py-1 font-mono text-[12px]"
                    onChange={(e) => setDraft({ ...draft, [k]: typeof cfg.values[k] === "number" ? Number(e.target.value) : e.target.value })} />
                )}
              </label>
            ))}
          </div>
        ))}
        <div className="card md:col-span-2">
          <p className="eyebrow mb-2">Model prices (USD per 1M tokens)</p>
          <p className="mb-3 text-[13px] text-muted-48">Filled in from the current AWS pricing pages by whoever deploys. Never hard-coded.</p>
          <textarea value={prices} onChange={(e) => setPrices(e.target.value)} rows={12} data-testid="cfg-model_prices"
            className="w-full rounded-md border border-hairline bg-pearl p-3 font-mono text-[12px]" />
        </div>
      </div>
      <div className="mt-6 flex items-center gap-4">
        <Button onClick={() => void save()} data-testid="save-config">Save</Button>
        {msg && <span className="text-[14px] text-muted-80" data-testid="config-msg">{msg}</span>}
      </div>
    </section>
  );
}
