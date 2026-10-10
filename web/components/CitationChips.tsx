"use client";
import { useState } from "react";
import type { Source } from "@/lib/types";

// Sources under an answer; a chip opens its snippet.
export function CitationChips({ sources }: { sources: Source[] }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!sources.length) return null;
  const s = open === null ? null : sources.find((x) => x.n === open);
  return (
    <div className="mt-2" data-testid="citations">
      <div className="flex flex-wrap gap-1.5">
        {sources.map((x) => (
          <button key={`${x.n}-${x.chunk_id}`} onClick={() => setOpen(open === x.n ? null : x.n)} data-testid="citation-chip"
            className={`rounded-pill border px-2.5 py-0.5 font-mono text-[11px] ${open === x.n ? "border-primary text-primary" : "border-hairline text-muted-80"}`}>
            [{x.n}] {x.title}{x.page ? ` · p. ${x.page}` : ""} · v{x.version}
          </button>
        ))}
      </div>
      {s && (
        <blockquote className="mt-2 whitespace-pre-wrap border-l-2 border-primary pl-3 text-[13px] text-muted-80" data-testid="citation-snippet">
          {s.snippet || "(snippet unavailable)"}
        </blockquote>
      )}
    </div>
  );
}
