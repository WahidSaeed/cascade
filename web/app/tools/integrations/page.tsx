"use client";
import { Button } from "@/components/Button";
import { PageHeader } from "@/components/Eyebrow";
import { api } from "@/lib/api";
import type { Integration } from "@/lib/types";
import { useData } from "@/lib/useData";

export default function IntegrationsPage() {
  const { data, reload, error } = useData(() => api<Integration[]>("/api/integrations"), "integrations", 5000);
  async function decide(id: string, ok: boolean) {
    await api(`/api/integrations/${id}/${ok ? "approve" : "reject"}`, { method: "POST" }).catch(() => undefined);
    reload();
  }
  return (
    <section>
      <PageHeader eyebrow="Tools · Integrations" title="Approve what artifacts may call."
        sub="Fixed targets, credentials in Secrets Manager, network egress only to that target." />
      {error && <p className="text-bad">{error}</p>}
      <div className="flex flex-col gap-3">
        {(data ?? []).map((i) => (
          <div key={i.id} className="card flex flex-wrap items-center justify-between gap-4" data-testid="integration-row" data-name={i.name}>
            <div>
              <p className="font-semibold">{i.name} <span className="badge badge-cache ml-2" data-testid="integration-state">{i.status}</span></p>
              <p className="font-mono text-[12px] text-muted-48">{i.type} · {i.role} · {i.target} · by {i.owner}</p>
              {(i.scan.findings?.length ?? 0) > 0 && <p className="text-[12px] text-warn">{i.scan.findings!.join("; ")}</p>}
            </div>
            {i.status === "pending" && (
              <div className="flex gap-2">
                <Button small onClick={() => void decide(i.id, true)} data-testid="approve-integration">Approve</Button>
                <Button small variant="danger" onClick={() => void decide(i.id, false)}>Reject</Button>
              </div>
            )}
          </div>
        ))}
        {data?.length === 0 && <p className="text-muted-48">No integrations registered.</p>}
      </div>
    </section>
  );
}
