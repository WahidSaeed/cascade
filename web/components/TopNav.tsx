"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Health } from "@/lib/types";
import { useUser } from "@/lib/user";

type Persona = { id: string; name: string; role: string };
const TABS = [
  { href: "/", label: "Console" },
  { href: "/savings/", label: "Savings" },
  { href: "/patterns/", label: "Patterns" },
  { href: "/tools/", label: "Tools" },
];

export function TopNav() {
  const path = usePathname();
  const { userId, setUserId } = useUser();
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthy, setHealthy] = useState<boolean | null>(null);

  useEffect(() => {
    api<Persona[]>("/api/users").then(setPersonas).catch(() => setPersonas([]));
    api<Health>("/api/health")
      .then((h) => { setHealth(h); setHealthy(h.status === "ok"); })
      .catch(() => setHealthy(false));
  }, []);

  return (
    <nav className="sticky top-0 z-40 flex h-[52px] items-center gap-7 bg-black px-5 text-white">
      <span className="font-mono text-sm font-bold tracking-[0.02em]">
        <span className="text-sky">◆</span> Cascade
      </span>
      <div className="flex gap-1">
        {TABS.map((t) => {
          const active = t.href === "/" ? path === "/" : path?.startsWith(t.href.replace(/\/$/, ""));
          return (
            <Link key={t.href} href={t.href}
              className={`rounded-sm px-3.5 py-[7px] text-[13px] ${active ? "bg-white/10 text-white" : "text-[#aeaeb2] hover:text-white"}`}>
              {t.label}
            </Link>
          );
        })}
      </div>
      <div className="ml-auto flex items-center gap-3 font-mono text-[11px] tracking-[0.04em] text-[#aeaeb2]">
        {health?.llm === "demo" && (
          <span className="rounded-pill border border-white/25 px-2 py-0.5 text-white" data-testid="demo-llm-badge">Demo LLM</span>
        )}
        <select aria-label="User" data-testid="user-switcher" value={userId}
          onChange={(e) => setUserId(e.target.value)}
          className="rounded-sm border border-white/20 bg-transparent px-2 py-1 text-white">
          {(personas.length ? personas : [{ id: userId, name: userId, role: "" }]).map((p) => (
            <option key={p.id} value={p.id} className="text-ink">{p.name}{p.role ? ` · ${p.role}` : ""}</option>
          ))}
        </select>
        <span className={`h-[7px] w-[7px] rounded-full ${healthy === false ? "bg-bad" : "bg-ok"}`} data-testid="health-dot" />
      </div>
    </nav>
  );
}
