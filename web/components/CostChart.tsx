"use client";
import { Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtDate, fmtUsd } from "@/lib/format";
import type { Bucket } from "@/lib/types";

// Baseline (what everything would have cost on the LLM) vs actual spend, per day.
export function CostChart({ data }: { data: Bucket[] }) {
  const rows = data.map((b) => ({ day: fmtDate(b.bucket), baseline: b.baseline_cost_usd, actual: b.actual_cost_usd }));
  return (
    <div className="h-[260px] w-full" data-testid="cost-chart">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#f0f0f0" vertical={false} />
          <XAxis dataKey="day" tick={{ fontSize: 11, fill: "#7a7a7a" }} axisLine={false} tickLine={false} />
          <YAxis tickFormatter={(v: number) => fmtUsd(v, 2)} tick={{ fontSize: 11, fill: "#7a7a7a" }} axisLine={false}
            tickLine={false} width={64} />
          <Tooltip formatter={(v) => fmtUsd(Number(v), 4)} contentStyle={{ borderRadius: 11, border: "1px solid #e0e0e0", fontSize: 12 }} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Area type="monotone" dataKey="baseline" name="Baseline (all on LLM)" stroke="#7a7a7a" fill="#f5f5f7" strokeWidth={2} />
          <Area type="monotone" dataKey="actual" name="Actual spend" stroke="#0066cc" fill="rgba(0,102,204,0.12)" strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
