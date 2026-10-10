import { fmtPct } from "@/lib/format";
import type { Iteration } from "@/lib/types";

function Md({ text }: { text: string }) {
  return (
    <div className="flex flex-col gap-1 text-[14px]">
      {text.split("\n").filter(Boolean).map((line, i) => {
        const html = line.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/^### /, "").replace(/^- /, "• ");
        return line.startsWith("### ")
          ? <p key={i} className="text-[17px] font-semibold" dangerouslySetInnerHTML={{ __html: html }} />
          : <p key={i} dangerouslySetInnerHTML={{ __html: html }} />;
      })}
    </div>
  );
}

// One iteration: the agent's report, its metrics, the confusion/error table and the model card.
export function IterationReport({ it }: { it: Iteration }) {
  const m = it.metrics;
  return (
    <div className="card" data-testid="iteration-report" data-iteration={it.n} data-kind={it.candidate?.kind}>
      <Md text={it.report_md} />
      {m && (
        <div className="mt-4 grid grid-cols-2 gap-3 font-mono text-[12px] md:grid-cols-4">
          <div><p className="text-muted-48">agreement</p><p className="text-[20px] font-bold text-primary" data-testid="agreement">{fmtPct(m.agreement)}</p></div>
          <div><p className="text-muted-48">coverage</p><p className="text-[20px]">{fmtPct(m.coverage ?? 1)}</p></div>
          <div><p className="text-muted-48">your labels</p><p className="text-[20px]">{m.user_label_accuracy === null ? "—" : fmtPct(m.user_label_accuracy)}</p></div>
          <div><p className="text-muted-48">latency · size</p><p className="text-[14px]">{m.latency_ms} ms · {m.size_mb} MB</p></div>
        </div>
      )}
      {m && Object.keys(m.per_class).length > 1 && (
        <p className="mt-3 font-mono text-[11px] text-muted-80">per class: {Object.entries(m.per_class).map(([k, v]) => `${k} ${fmtPct(v, 0)}`).join(" · ")}</p>
      )}
      {(it.confusion?.length || it.errors.length) ? (
        <table className="mt-4 w-full text-[12px]" data-testid="error-table">
          <thead><tr className="text-left text-muted-48"><th className="font-normal">Prompt</th><th className="font-normal">Expected</th><th className="font-normal">Got</th></tr></thead>
          <tbody>
            {it.errors.slice(0, 6).map((e, i) => (
              <tr key={i} className="border-t border-divider align-top">
                <td className="py-1 pr-2">{e.prompt.slice(0, 90)}</td><td className="font-mono">{e.expected}</td>
                <td className="font-mono">{e.got}{e.kind === "low_confidence" ? ` (${e.confidence})` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <p className="mt-3 font-mono text-[11px] text-muted-48" data-testid="model-card">
        {it.candidate?.kind} · {JSON.stringify(it.candidate?.params ?? {})}
      </p>
    </div>
  );
}
