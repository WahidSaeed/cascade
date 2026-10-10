export function StatTile({ label, value, note, testId, estimated = false }:
  { label: string; value: string; note?: string; testId?: string; estimated?: boolean }) {
  return (
    <div className="card flex flex-col gap-2" data-testid={testId}>
      <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted-48">
        {label}{estimated && <span title="Includes estimated figures"> · est.</span>}
      </span>
      <span className="metric !text-[32px]" data-testid={testId ? `${testId}-value` : undefined}>{value}</span>
      {note && <span className="text-[13px] text-muted-48">{note}</span>}
    </div>
  );
}
