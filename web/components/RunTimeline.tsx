"use client";
import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";
import type { RunEvent } from "@/lib/types";

// Live steps over Server-Sent Events; `initial` is the persisted timeline (the run survives restarts).
export function RunTimeline({ runId, initial, onStatus }:
  { runId: string; initial: RunEvent[]; onStatus: (status: string) => void }) {
  const [events, setEvents] = useState<RunEvent[]>(initial);
  useEffect(() => {
    const last = initial.length ? initial[initial.length - 1].n : 0;
    const es = new EventSource(`${API_BASE}/api/agent/runs/${runId}/events?after=${last}`);
    es.addEventListener("step", (e) => {
      const ev = JSON.parse((e as MessageEvent).data) as RunEvent;
      setEvents((all) => (all.some((x) => x.n === ev.n) ? all : [...all, ev]));
    });
    es.addEventListener("status", (e) => onStatus(JSON.parse((e as MessageEvent).data).status));
    return () => es.close();
  }, [runId, initial, onStatus]);
  return (
    <ol className="flex flex-col gap-2" data-testid="timeline">
      {events.map((e) => (
        <li key={e.n} className="flex gap-3 text-[13px]" data-testid="timeline-step">
          <span className="w-16 shrink-0 font-mono text-[11px] uppercase tracking-[0.08em] text-muted-48">{e.kind}</span>
          <span className="text-muted-80">{e.text}</span>
        </li>
      ))}
    </ol>
  );
}
