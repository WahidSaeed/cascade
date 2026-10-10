import { fmtInt, fmtPct } from "@/lib/format";
import type { Route } from "@/lib/types";

const ORDER: Route[] = ["cache", "artifact", "llm"];
const COLOR: Record<Route, string> = { cache: "bg-hairline", artifact: "bg-primary", llm: "bg-ink" };
const LABEL: Record<Route, string> = { cache: "Cache", artifact: "Artifact", llm: "LLM" };

export function RouteMix({ byRoute }: { byRoute: Partial<Record<Route, number>> }) {
  const total = ORDER.reduce((s, r) => s + (byRoute[r] ?? 0), 0) || 1;
  return (
    <div data-testid="route-mix">
      <div className="flex h-3 w-full overflow-hidden rounded-pill bg-divider">
        {ORDER.map((r) => (
          <div key={r} className={COLOR[r]} style={{ width: `${((byRoute[r] ?? 0) / total) * 100}%` }} />
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-5 font-mono text-[12px] text-muted-80">
        {ORDER.map((r) => (
          <span key={r} className="flex items-center gap-1.5" data-testid={`route-mix-${r}`}>
            <span className={`inline-block h-2 w-2 rounded-full ${COLOR[r]}`} />
            {LABEL[r]} {fmtInt(byRoute[r] ?? 0)} · {fmtPct((byRoute[r] ?? 0) / total)}
          </span>
        ))}
      </div>
    </div>
  );
}
