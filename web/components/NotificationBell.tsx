"use client";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { useData } from "@/lib/useData";
import { useUser } from "@/lib/user";

type Note = { id: string; kind: string; title: string; body: string; link: string; read: boolean; created_at: number };

// Recognized patterns, runs waiting for feedback, drift and re-verification alerts.
export function NotificationBell() {
  const { userId } = useUser();
  const { data, reload } = useData(() => api<Note[]>("/api/notifications"), `notes-${userId}`, 5000);
  const [open, setOpen] = useState(false);
  const notes = data ?? [];
  const unread = notes.filter((n) => !n.read).length;
  return (
    <div className="relative">
      <button onClick={() => setOpen(!open)} aria-label="Notifications" data-testid="bell"
        className="relative px-1 text-[15px] text-white">
        🔔{unread > 0 && (
          <span className="absolute -right-1.5 -top-1 rounded-pill bg-primary px-1.5 font-mono text-[9px] text-white"
            data-testid="bell-count">{unread}</span>
        )}
      </button>
      {open && (
        <div className="shadow-product absolute right-0 top-8 z-50 w-[360px] rounded-md bg-canvas p-2 text-ink" data-testid="notifications">
          {notes.length === 0 && <p className="p-3 text-[13px] text-muted-48">Nothing new.</p>}
          {notes.slice(0, 8).map((n) => (
            <Link key={n.id} href={n.link || "/patterns/"} data-testid="notification"
              onClick={() => { void api(`/api/notifications/${n.id}/read`, { method: "POST" }).then(reload); setOpen(false); }}
              className={`block rounded-sm p-3 text-[13px] hover:bg-parchment ${n.read ? "text-muted-48" : ""}`}>
              <span className="block font-semibold tracking-normal">{n.title}</span>
              {n.body && <span className="block font-mono text-[11px] text-muted-48">{n.body}</span>}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
