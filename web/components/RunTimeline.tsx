"use client";
import type { RunEvent } from "@/lib/types";

// The run's timeline, as persisted (the run survives restarts). The run page polls the run, so new steps arrive
// with each poll; a per-render event stream would reconnect on every poll, which proxies (CloudFront) reject.
export function RunTimeline({ initial }: { runId?: string; initial: RunEvent[]; onStatus?: (status: string) => void }) {
  return (
    <ol className="flex flex-col gap-2" data-testid="timeline">
      {initial.map((e) => (
        <li key={e.n} className="flex gap-3 text-[13px]" data-testid="timeline-step">
          <span className="w-16 shrink-0 font-mono text-[11px] uppercase tracking-[0.08em] text-muted-48">{e.kind}</span>
          <span className="text-muted-80">{e.text}</span>
        </li>
      ))}
    </ol>
  );
}
