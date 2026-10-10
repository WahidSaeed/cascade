"use client";
import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { PageHeader } from "@/components/Eyebrow";
import { PatternCard } from "@/components/PatternCard";
import { PatternDrawer } from "@/components/PatternDrawer";
import { api } from "@/lib/api";
import type { Pattern } from "@/lib/types";
import { useData } from "@/lib/useData";

function PatternsView() {
  const { data, reload } = useData(() => api<Pattern[]>("/api/patterns"), "patterns");
  const patterns = data ?? [];
  const params = useSearchParams();
  const router = useRouter();
  const openId = params.get("id");

  const open = patterns.find((p) => p.id === openId);

  return (
    <section>
      <PageHeader eyebrow="Patterns" title="The artifact library."
        sub="Recurring requests compiled into rules and models — verified against the LLM, approved by a person." />
      <div className="grid gap-4 md:grid-cols-3" data-testid="pattern-grid">
        {patterns.map((p) => <PatternCard key={p.id} p={p} onOpen={() => router.push(`/patterns/?id=${p.id}`)} />)}
        {patterns.length === 0 && <p className="text-muted-48">No patterns yet.</p>}
      </div>
      {open && <PatternDrawer key={`${open.id}-${open.version}-${open.updated_at}`} p={open}
        onClose={() => router.push("/patterns/")} onChange={reload} />}
    </section>
  );
}

export default function PatternsPage() {
  return <Suspense><PatternsView /></Suspense>;
}
