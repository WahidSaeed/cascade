"use client";
import { CostChart } from "@/components/CostChart";
import { PageHeader } from "@/components/Eyebrow";
import { RouteMix } from "@/components/RouteMix";
import { StatTile } from "@/components/StatTile";
import { api } from "@/lib/api";
import { fmtInt, fmtPct, fmtUsd } from "@/lib/format";
import { useData } from "@/lib/useData";
import type { Bucket, PatternRow, RagMetrics, Summary, UserRow } from "@/lib/types";

const th = "py-2 pr-4 text-left font-mono text-[11px] uppercase tracking-[0.1em] text-muted-48 font-normal";
const td = "py-2.5 pr-4 border-t border-divider";

export default function SavingsPage() {
  const { data, error } = useData(async () => {
    const [summary, series, users, patterns, rag] = await Promise.all([
      api<Summary>("/api/ledger/summary"),
      api<Bucket[]>("/api/ledger/timeseries?bucket=day"),
      api<UserRow[]>("/api/ledger/by-user"),
      api<PatternRow[]>("/api/ledger/by-pattern"),
      api<RagMetrics>("/api/rag/metrics"),
    ]);
    return { summary, series, users, patterns, rag };
  }, "savings", 15000);

  if (error) return <p className="text-bad">{error}</p>;
  if (!data) return <p className="text-muted-48">Loading the ledger…</p>;
  const { summary: s, series, users, patterns, rag } = data;
  const estimated = s.estimated_share > 0 || (s.estimate_methods.pattern_measured ?? 0) > 0;
  const mix = s.input_mix;
  const mixTotal = mix.prompt + mix.context + mix.history + mix.system || 1;

  return (
    <section data-testid="savings">
      <PageHeader eyebrow="Savings" title="Every token, accounted for."
        sub="Tokens spent, tokens avoided and money saved — net of the cost of building and checking the artifacts." />
      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        <StatTile testId="tile-spent" label="Tokens spent" value={fmtInt(s.tokens_spent)}
          note={`incl. ${fmtInt(s.overhead_tokens)} overhead`} />
        <StatTile testId="tile-avoided" label="Tokens avoided" value={fmtInt(s.tokens_avoided)} estimated={estimated}
          note={`${fmtInt(s.requests)} requests`} />
        <StatTile testId="tile-reduction" label="Token reduction" value={fmtPct(s.token_reduction)} estimated={estimated} />
        <StatTile testId="tile-net" label="Net saved" value={fmtUsd(s.net_saved_usd, 2)} estimated={estimated}
          note={`gross ${fmtUsd(s.gross_saved_usd, 2)} − overhead ${fmtUsd(s.overhead_cost_usd, 2)}`} />
        <StatTile testId="tile-rate" label="Savings rate" value={fmtPct(s.savings_rate)} estimated={estimated}
          note={`of ${fmtUsd(s.baseline_cost_usd, 2)} baseline`} />
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-3">
        <div className="card md:col-span-2">
          <p className="eyebrow mb-4">Baseline vs actual cost</p>
          <CostChart data={series} />
        </div>
        <div className="card flex flex-col gap-6">
          <div>
            <p className="eyebrow mb-4">Route mix</p>
            <RouteMix byRoute={s.by_route} />
          </div>
          <div data-testid="input-mix">
            <p className="eyebrow mb-3">Input mix</p>
            <div className="font-mono text-[12px] leading-6 text-muted-80">
              {(["prompt", "context", "history", "system"] as const).map((k) => (
                <div key={k} className="flex justify-between">
                  <span>{k === "prompt" ? "question" : k}</span>
                  <span>{fmtInt(mix[k])} · {fmtPct(mix[k] / mixTotal)}</span>
                </div>
              ))}
              <div className="flex justify-between text-muted-48">
                <span>prompt-cache read / write</span><span>{fmtInt(mix.cache_read)} / {fmtInt(mix.cache_write)}</span>
              </div>
            </div>
          </div>
          <div data-testid="knowledge-savings">
            <p className="eyebrow mb-3">Knowledge</p>
            <div className="font-mono text-[12px] leading-6 text-muted-80">
              <div className="flex justify-between"><span>questions</span><span>{fmtInt(rag.knowledge_requests)}</span></div>
              <div className="flex justify-between"><span>cache hit rate</span><span data-testid="knowledge-hit-rate">{fmtPct(rag.knowledge_cache_hit_rate)}</span></div>
              <div className="flex justify-between"><span>context tokens avoided</span><span data-testid="context-avoided">{fmtInt(s.context_tokens_avoided)}</span></div>
              <div className="flex justify-between"><span>citation rate</span><span>{fmtPct(rag.citation_rate)}</span></div>
            </div>
          </div>
        </div>
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-2">
        <div className="card overflow-x-auto">
          <p className="eyebrow mb-3">Per pattern · payback</p>
          <table className="w-full text-[14px]" data-testid="pattern-table">
            <thead><tr><th className={th}>Pattern</th><th className={th}>Requests</th><th className={th}>Net saved</th><th className={th}>Payback</th></tr></thead>
            <tbody>
              {patterns.map((p) => (
                <tr key={p.pattern_id}>
                  <td className={td}>{p.pattern_name}</td>
                  <td className={`${td} font-mono`}>{fmtInt(p.requests)}</td>
                  <td className={`${td} font-mono`}>{fmtUsd(p.net_saved_usd, 4)}</td>
                  <td className={`${td} text-[13px] text-muted-80`} data-testid="payback">
                    {p.paid_back ? `paid back after ${fmtInt(p.paid_back_after_requests)} requests`
                      : `not yet (overhead ${fmtUsd(p.overhead_cost_usd, 4)})`}
                  </td>
                </tr>
              ))}
              {patterns.length === 0 && <tr><td className={td} colSpan={4}>No artifact traffic yet.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="card overflow-x-auto">
          <p className="eyebrow mb-3">Per user</p>
          <table className="w-full text-[14px]" data-testid="user-table">
            <thead><tr><th className={th}>User</th><th className={th}>Requests</th><th className={th}>Spent</th><th className={th}>Avoided</th><th className={th}>Net saved</th></tr></thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.user_id} data-testid={`user-row-${u.user_id}`}>
                  <td className={td}>{u.user_id}</td>
                  <td className={`${td} font-mono`}>{fmtInt(u.requests)}</td>
                  <td className={`${td} font-mono`}>{fmtInt(u.tokens_spent)}</td>
                  <td className={`${td} font-mono`}>{fmtInt(u.tokens_avoided)}</td>
                  <td className={`${td} font-mono`}>{fmtUsd(u.net_saved_usd, 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {estimated && (
        <p className="mt-4 text-[13px] text-muted-48" data-testid="estimate-note">
          Figures marked “est.” include avoided tokens estimated from each pattern’s measured LLM usage
          ({fmtInt(s.estimate_methods.pattern_measured ?? 0)} requests) or a length heuristic
          ({fmtInt(s.estimate_methods.heuristic ?? 0)}); cache hits use the cached answer’s real usage
          ({fmtInt(s.estimate_methods.cache_exact ?? 0)}).
        </p>
      )}
    </section>
  );
}
