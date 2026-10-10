"use client";
import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { PageHeader } from "@/components/Eyebrow";
import { IntakeForm } from "@/components/IntakeForm";
import { api } from "@/lib/api";
import { fmtInt, fmtUsd } from "@/lib/format";
import type { Intake } from "@/lib/types";
import { useData } from "@/lib/useData";

function IntakeView() {
  const id = useSearchParams().get("id") ?? "";
  const router = useRouter();
  const { data, error } = useData(() => api<Intake>(`/api/patterns/${id}/intake`), `intake-${id}`);
  if (error) return <p className="text-bad">{error}</p>;
  if (!data) return <p className="text-muted-48">Loading…</p>;
  return (
    <section>
      <PageHeader eyebrow="Pattern recognized" title={data.pattern.name}
        sub="Before the agent builds anything, tell it what you know. Nothing runs until you press Start." />
      <div className="mb-6 grid gap-4 md:grid-cols-4" data-testid="intake-summary">
        <div className="card"><p className="eyebrow">Frequency</p><p className="metric !text-[28px]">{fmtInt(Math.round(data.frequency_per_day))}/day</p></div>
        <div className="card"><p className="eyebrow">Tokens / day</p><p className="metric !text-[28px]">{fmtInt(Math.round(data.projected_tokens_per_day))}</p></div>
        <div className="card"><p className="eyebrow">Projected / month</p><p className="metric !text-[28px]">{fmtUsd(data.projected_monthly_savings_usd, 2)}</p></div>
        <div className="card"><p className="eyebrow">Task type</p><p className="metric !text-[22px]">{data.pattern.task_type}</p></div>
      </div>
      <div className="card mb-6">
        <p className="eyebrow mb-2">Suggested plan</p>
        <p data-testid="suggested-plan">{data.suggested_plan}</p>
        <p className="eyebrow mb-2 mt-4">Examples (redacted)</p>
        <ul className="font-mono text-[12px] text-muted-80">{data.examples.slice(0, 5).map((e, i) => <li key={i}>{e}</li>)}</ul>
      </div>
      <IntakeForm intake={data} onStarted={(run) => router.push(`/agents/run/?id=${run.id}`)} />
    </section>
  );
}

export default function IntakePage() {
  return <Suspense><IntakeView /></Suspense>;
}
