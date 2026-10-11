"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { onPatternsChanged } from "@/lib/bus";
import type { Pattern } from "@/lib/types";
import { useData } from "@/lib/useData";

const GROUPS: [string, string[]][] = [
  ["Serving", ["approved"]],
  ["In review", ["draft", "shadow", "awaiting_feedback", "agent_running", "awaiting_intake"]],
  ["Recognized", ["recognized"]],
  ["Archived", ["rejected", "stopped"]],
];
const STATUS_CLS: Record<string, string> = {
  approved: "text-primary", shadow: "text-warn", rejected: "text-muted-48", recognized: "text-ink",
};
const head = "px-3 pb-1 pt-3 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-48";

export function filesFor(p: Pattern): string[] {
  if (p.kind === "rule") return ["handler.py", "examples.txt"];
  if (p.kind === "ml" || p.kind === "dl") return ["model.py", "model_card.json", "examples.txt"];
  return ["examples.txt"];
}

// The Patterns side panel: every pattern as a tree item (status, kind, alerts), its files under the open one.
export function PatternExplorer() {
  const [tick, setTick] = useState(0);
  useEffect(() => onPatternsChanged(() => setTick((t) => t + 1)), []);
  const { data } = useData(() => api<Pattern[]>("/api/patterns"), `explorer-${tick}`, 4000);
  const params = useSearchParams();
  const router = useRouter();
  const openId = params.get("id");
  const openFile = params.get("file");
  const patterns = data ?? [];

  return (
    <div className="pb-6" data-testid="pattern-explorer">
      <div className="flex items-center justify-between px-3 pt-3">
        <span className="font-mono text-[11px] font-bold uppercase tracking-[0.14em] text-muted-80">Explorer</span>
        <button className="font-mono text-[11px] text-primary" onClick={() => router.push("/patterns/")}
          data-testid="explorer-overview">overview</button>
      </div>
      {GROUPS.map(([title, statuses]) => {
        const items = patterns.filter((p) => statuses.includes(p.status));
        if (!items.length) return null;
        return (
          <div key={title}>
            <p className={head}>▾ {title}</p>
            {items.map((p) => {
              const open = p.id === openId;
              return (
                <div key={p.id}>
                  <button onClick={() => router.push(`/patterns/?id=${p.id}`)}
                    className={`flex w-full flex-col px-3 py-1.5 text-left ${open ? "bg-primary/10" : "hover:bg-divider"}`}
                    data-testid="pattern-card" data-pattern-id={p.id} data-status={p.status}>
                    <span className="flex items-center gap-2">
                      <span className="w-5 shrink-0 font-mono text-[10px] uppercase text-muted-48">{p.kind ?? "·"}</span>
                      <span className="truncate text-[13px] text-ink">{p.name}</span>
                      <span className={`ml-auto shrink-0 font-mono text-[10px] ${STATUS_CLS[p.status] ?? "text-muted-80"}`}
                        data-testid="pattern-status">{p.status.replace("_", " ")}</span>
                    </span>
                    {p.alert && <span className="pl-7 text-[11px] leading-snug text-warn" data-testid="pattern-alert">⚠ {p.alert}</span>}
                    {p.not_fully_deterministic && <span className="pl-7 text-[11px] text-warn" data-testid="not-deterministic">not fully deterministic</span>}
                  </button>
                  {open && filesFor(p).map((f, i) => {
                    const on = (openFile ?? filesFor(p)[0]) === f || (!openFile && i === 0);
                    return (
                      <button key={f} onClick={() => router.push(`/patterns/?id=${p.id}&file=${f}`)}
                        className={`block w-full py-1 pl-10 text-left font-mono text-[12px] ${on ? "text-primary" : "text-muted-80 hover:text-ink"}`}
                        data-testid="explorer-file">{f}</button>
                    );
                  })}
                </div>
              );
            })}
          </div>
        );
      })}
      {patterns.length === 0 && <p className="px-3 pt-3 text-[13px] text-muted-48">No patterns yet.</p>}
    </div>
  );
}
