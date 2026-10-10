import type { Pattern } from "@/lib/types";
import { fmtInt, fmtPct, fmtUsd } from "@/lib/format";

const STATUS_CLS: Record<string, string> = {
  approved: "bg-primary/10 text-primary",
  shadow: "bg-divider text-warn",
  rejected: "bg-divider text-muted-48",
  recognized: "bg-ink text-white",
};

export function PatternCard({ p, onOpen }: { p: Pattern; onOpen: () => void }) {
  return (
    <button onClick={onOpen} className="card flex flex-col gap-3 text-left transition-colors hover:border-primary"
      data-testid="pattern-card" data-pattern-id={p.id} data-status={p.status}>
      <div className="flex items-center justify-between gap-2">
        <span className={`badge ${STATUS_CLS[p.status] ?? "bg-divider text-muted-80"}`} data-testid="pattern-status">{p.status.replace("_", " ")}</span>
        <span className="font-mono text-[11px] uppercase tracking-[0.1em] text-muted-48">
          {p.kind ?? "—"} · {p.task_type ?? "untyped"}
        </span>
      </div>
      <h3 className="text-[21px] leading-tight">{p.name}</h3>
      <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[12px] text-muted-80">
        <span>score {p.verification_score == null ? "—" : fmtPct(p.verification_score)}</span>
        <span>hits {fmtInt(p.hits)}</span>
        {(p.projected_monthly_savings_usd ?? 0) > 0 && <span>~{fmtUsd(p.projected_monthly_savings_usd, 2)}/mo</span>}
      </div>
      {p.alert && <p className="text-[13px] text-warn" data-testid="pattern-alert">⚠ {p.alert}</p>}
      {(p.leak_warnings?.length ?? 0) > 0 && <p className="text-[13px] text-bad">{(p.leak_warnings ?? []).length} leak warning(s)</p>}
    </button>
  );
}
