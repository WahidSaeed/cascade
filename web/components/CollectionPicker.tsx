"use client";
import type { Collection } from "@/lib/types";

const ACCESS: Record<string, string> = { org: "everyone", group: "group", private: "private" };

// Knowledge collections attached to a conversation (only collections the user can read are offered).
export function CollectionPicker({ collections, selected, onChange }:
  { collections: Collection[]; selected: string[]; onChange: (ids: string[]) => void }) {
  const toggle = (id: string) => onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="collection-picker">
      <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted-48">Knowledge</span>
      {collections.map((c) => {
        const on = selected.includes(c.id);
        return (
          <button key={c.id} onClick={() => toggle(c.id)} data-testid={`pick-${c.id}`} aria-pressed={on}
            className={`rounded-pill border px-3 py-1 text-[13px] transition-colors ${on ? "border-primary bg-primary text-white" : "border-hairline bg-canvas text-muted-80 hover:border-primary"}`}>
            {c.name} <span className={`font-mono text-[10px] ${on ? "text-white/70" : "text-muted-48"}`}>· {ACCESS[c.access]}</span>
          </button>
        );
      })}
      {collections.length > 1 && (
        <button className="text-[12px] text-primary" data-testid="pick-all"
          onClick={() => onChange(selected.length === collections.length ? [] : collections.map((c) => c.id))}>
          {selected.length === collections.length ? "none" : "all"}
        </button>
      )}
    </div>
  );
}
