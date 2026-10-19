"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense, useEffect, useState, useSyncExternalStore } from "react";
import { AUTH_MODE, api, idToken, signIn, signedInEmail } from "@/lib/api";
import { NotificationBell } from "@/components/NotificationBell";
import { PatternExplorer } from "@/components/PatternExplorer";
import type { Health } from "@/lib/types";
import { useUser } from "@/lib/user";

type Persona = { id: string; name: string; role: string };
type Item = { href: string; label: string; icon: React.ReactNode };

const I = (d: string) => (
  <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.6"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);
const ITEMS: Item[] = [
  { href: "/", label: "Console", icon: I("M4 5h16v11H8l-4 4z") },
  { href: "/patterns/", label: "Patterns", icon: I("M8 6l-5 6 5 6M16 6l5 6-5 6M14 4l-4 16") },
  { href: "/knowledge/", label: "Knowledge", icon: I("M4 4h7a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H4zM20 4h-3a3 3 0 0 0-3 3") },
  { href: "/savings/", label: "Savings", icon: I("M4 20V10M10 20V4M16 20v-7M22 20H2") },
  { href: "/tools/", label: "Tools", icon: I("M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12l2-1-1-3-2 .3-1.4-1.4.3-2-3-1-1 2h-2l-1-2-3 1 .3 2L6.8 7.3 4.8 7l-1 3 2 1v2l-2 1 1 3 2-.3 1.4 1.4-.3 2 3 1 1-2h2l1 2 3-1-.3-2 1.4-1.4 2 .3 1-3-2-1z") },
];
const DEMO: Item = { href: "/demo/", label: "Demo", icon: I("M7 4l13 8-13 8z") };

function active(path: string | null, href: string): boolean {
  return href === "/" ? path === "/" : !!path?.startsWith(href.replace(/\/$/, ""));
}

const noSubscribe = () => () => {};

// The workspace shell: title bar, activity bar, side panel (per section), editor area and status bar.
export function Workbench({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { userId, setUserId } = useUser();
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [sideOpen, setSideOpen] = useState(true);
  // who is signed in (Cognito) — and whether we are past hydration: the static HTML is rendered without a session,
  // so session-dependent text (sign-in link, user) renders only on the client, after mount
  const [me, setMe] = useState<{ user_id: string; is_admin: boolean } | null>(null);
  const mounted = useSyncExternalStore(noSubscribe, () => true, () => false);

  useEffect(() => {
    if (AUTH_MODE === "cognito" && idToken()) {
      api<{ user_id: string; is_admin: boolean }>("/api/me").then(setMe).catch(() => setMe(null));
    }
    api<Persona[]>("/api/users").then(setPersonas).catch(() => setPersonas([]));
    api<Health>("/api/health")
      .then((h) => { setHealth(h); setHealthy(h.status === "ok"); })
      .catch(() => setHealthy(false));
  }, []);

  const items = [...ITEMS, ...(health?.demo ? [DEMO] : [])];
  const section = items.find((t) => active(path, t.href));
  const isPatterns = !!path?.startsWith("/patterns") && !path.includes("/intake");
  const side = isPatterns ? <Suspense><PatternExplorer /></Suspense> : null;
  const persona = personas.find((p) => p.id === userId);

  return (
    <div className="flex h-screen flex-col">
      <div className="flex h-[38px] shrink-0 items-center gap-4 bg-black px-3 text-white" data-testid="title-bar">
        <span className="font-mono text-[13px] font-bold tracking-[0.02em]"><span className="text-sky">◆</span> Cascade</span>
        <span className="font-mono text-[11px] text-body-muted" data-testid="breadcrumb">
          workspace <span className="text-muted-48">/</span> {section?.label.toLowerCase() ?? "…"}
        </span>
        <div className="ml-auto flex items-center gap-3 font-mono text-[11px] tracking-[0.04em] text-body-muted">
          {health?.llm === "demo" && (
            <span className="rounded-pill border border-white/25 px-2 py-0.5 text-white" data-testid="demo-llm-badge">Demo LLM</span>
          )}
          <NotificationBell />
          {mounted && AUTH_MODE === "cognito" && !idToken() && (
            <button onClick={() => void signIn()} className="text-white underline" data-testid="sign-in">Sign in</button>
          )}
          {AUTH_MODE !== "cognito" && <select aria-label="User" data-testid="user-switcher" value={userId}
            onChange={(e) => setUserId(e.target.value)}
            className="rounded-sm border border-white/20 bg-transparent px-2 py-0.5 text-white">
            {(personas.length ? personas : [{ id: userId, name: userId, role: "" }]).map((p) => (
              <option key={p.id} value={p.id} className="text-ink">{p.name}{p.role ? ` · ${p.role}` : ""}</option>
            ))}
          </select>}
          <span className={`h-[7px] w-[7px] rounded-full ${healthy === false ? "bg-bad" : "bg-ok"}`} data-testid="health-dot" />
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <nav className="flex w-[52px] shrink-0 flex-col items-center gap-1 bg-black pt-2" data-testid="activity-bar">
          {items.map((t) => {
            const on = active(path, t.href);
            return (
              <Link key={t.href} href={t.href} aria-label={t.label} title={t.label}
                onClick={(e) => { if (on && side) { e.preventDefault(); setSideOpen(!sideOpen); } }}
                className={`relative flex h-11 w-11 items-center justify-center rounded-sm ${on ? "text-white" : "text-muted-48 hover:text-white"}`}>
                {on && <span className="absolute left-[-4px] top-2 h-7 w-[2px] bg-sky" />}
                {t.icon}
              </Link>
            );
          })}
        </nav>
        {side && sideOpen && (
          <aside className="flex w-[264px] shrink-0 flex-col overflow-y-auto border-r border-hairline bg-pearl" data-testid="side-panel">
            {side}
          </aside>
        )}
        <main className="min-w-0 flex-1 overflow-y-auto bg-parchment" data-testid="editor-area">
          {isPatterns ? children : <div className="mx-auto max-w-[1180px] px-6 pb-20 pt-9">{children}</div>}
        </main>
      </div>

      <footer className="flex h-[24px] shrink-0 items-center gap-4 bg-primary px-3 font-mono text-[11px] text-white" data-testid="status-bar">
        <span>◆ {health?.db ?? "…"}</span>
        <span>llm: {health?.llm ?? "…"}</span>
        <span>artifacts: {health?.artifact_runtime ?? "…"}</span>
        <span className="ml-auto" data-testid="status-user">{!mounted ? "" : AUTH_MODE === "cognito"
          ? (me ? `${signedInEmail() ?? me.user_id}${me.is_admin ? " · admin" : ""}` : "signed out")
          : persona ? `${persona.name} · ${persona.role}` : userId}</span>
        <span>{healthy === false ? "API offline" : "API ok"}</span>
      </footer>
    </div>
  );
}
