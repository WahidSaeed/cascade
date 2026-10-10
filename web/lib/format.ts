export const fmtInt = (n: number | null | undefined) => (n ?? 0).toLocaleString("en-US");

export function fmtUsd(n: number | null | undefined, digits?: number): string {
  const v = n ?? 0;
  const d = digits ?? (Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 1 ? 2 : 4);
  return `${v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`;
}

export const fmtPct = (n: number | null | undefined, digits = 1) => `${((n ?? 0) * 100).toFixed(digits)}%`;

export function fmtMs(ms: number | null | undefined): string {
  const v = ms ?? 0;
  return v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${v < 10 ? v.toFixed(1) : Math.round(v)} ms`;
}

export const fmtDate = (ts: number) =>
  new Date(ts * 1000).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
