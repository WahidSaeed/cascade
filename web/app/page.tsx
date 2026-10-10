"use client";
import { useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/Eyebrow";
import { Button } from "@/components/Button";
import { CitationChips } from "@/components/CitationChips";
import { CollectionPicker } from "@/components/CollectionPicker";
import { RouteBadge } from "@/components/RouteBadge";
import { TokenNote } from "@/components/TokenNote";
import { api } from "@/lib/api";
import { fmtMs } from "@/lib/format";
import type { ChatResponse, Collection } from "@/lib/types";
import { useData } from "@/lib/useData";
import { useUser } from "@/lib/user";

type Msg = { role: "user"; text: string } | { role: "bot"; r: ChatResponse } | { role: "error"; text: string };
type Thread = { conversationId: string | null; msgs: Msg[]; collections: string[] };
const EMPTY: Thread = { conversationId: null, msgs: [], collections: [] };

const STAGE_LABEL: Record<string, string> = {
  exact_cache: "Exact cache",
  semantic_cache: "Semantic cache",
  recognizer: "Pattern recognizer",
  none: "No match — heavy LLM",
  skipped: "Follow-up — caches skipped",
};

function Why({ r }: { r: ChatResponse }) {
  const l = r.light;
  const stages = Object.entries(l.latency_ms_by_stage ?? {});
  return (
    <div className="mt-2 rounded-md border border-hairline bg-pearl p-3 font-mono text-[11px] leading-relaxed text-muted-80"
      data-testid="why-panel">
      <div><span className="text-muted-48">decided by</span> {STAGE_LABEL[l.stage_hit] ?? l.stage_hit}</div>
      {l.reason && <div><span className="text-muted-48">why</span> {l.reason}</div>}
      <div className="break-all"><span className="text-muted-48">template</span> {l.template}</div>
      {Object.keys(l.slots ?? {}).length > 0 && (
        <div className="break-all"><span className="text-muted-48">slots</span> {JSON.stringify(l.slots)}</div>
      )}
      {(l.volatile || l.context_dependent) && (
        <div>{l.volatile && "time-sensitive · not cached "}{l.context_dependent && "follow-up · uses history"}</div>
      )}
      {stages.length > 0 && (
        <div><span className="text-muted-48">stages</span> {stages.map(([k, v]) => `${k} ${fmtMs(v)}`).join(" · ")}</div>
      )}
    </div>
  );
}

function BotMessage({ r }: { r: ChatResponse }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="max-w-[74%] self-start" data-testid="bot-message">
      <div className="whitespace-pre-wrap rounded-lg rounded-bl-sm border border-hairline bg-canvas px-[18px] py-[13px] text-base"
        data-testid="answer">{r.answer}</div>
      <div className="mt-[7px] flex flex-wrap items-center gap-2.5 font-mono text-[11px] tracking-[0.03em] text-muted-48">
        <RouteBadge route={r.route} />
        <span data-testid="latency">{fmtMs(r.latency_ms)}</span>
        <TokenNote r={r} />
        {r.pattern_name && <span>· {r.pattern_name}</span>}
        {r.not_fully_deterministic && <span className="text-warn">· not fully deterministic</span>}
        <button className="text-primary hover:underline" onClick={() => setOpen(!open)} data-testid="why-toggle">
          {open ? "hide why" : "why?"}
        </button>
      </div>
      <CitationChips sources={r.sources ?? []} />
      {open && <Why r={r} />}
    </div>
  );
}

export default function ConsolePage() {
  const { userId } = useUser();
  const [threads, setThreads] = useState<Record<string, Thread>>({});
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const thread = threads[userId] ?? EMPTY;
  const cols = useData(() => api<Collection[]>("/api/collections"), `console-cols-${userId}`);

  useEffect(() => {
    // a block body: newer browsers return a Promise from scrollIntoView, which must not become the cleanup
    end.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [thread.msgs.length]);

  const update = (uid: string, f: (t: Thread) => Thread) =>
    setThreads((all) => ({ ...all, [uid]: f(all[uid] ?? EMPTY) }));

  async function send() {
    const prompt = input.trim();
    if (!prompt || busy) return;
    const uid = userId;
    setInput("");
    setBusy(true);
    update(uid, (t) => ({ ...t, msgs: [...t.msgs, { role: "user", text: prompt }] }));
    try {
      const r = await api<ChatResponse>("/api/chat", {
        method: "POST",
        json: { prompt, conversation_id: threads[uid]?.conversationId ?? null,
          collection_ids: threads[uid]?.collections ?? [] },
      });
      update(uid, (t) => ({ ...t, conversationId: r.conversation_id, msgs: [...t.msgs, { role: "bot", r }] }));
    } catch (e) {
      update(uid, (t) => ({ ...t, msgs: [...t.msgs, { role: "error", text: String(e) }] }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex min-h-[calc(100vh-160px)] flex-col">
      <PageHeader eyebrow="Console" title="Ask anything." sub="Every prompt is routed to the cheapest path that's still correct." />
      {(cols.data?.length ?? 0) > 0 && (
        <div className="mb-6">
          <CollectionPicker collections={cols.data ?? []} selected={thread.collections}
            onChange={(ids) => update(userId, (t) => ({ ...t, collections: ids }))} />
        </div>
      )}
      <div className="flex flex-1 flex-col gap-[18px] pb-5" data-testid="messages">
        {thread.msgs.length === 0 && (
          <p className="text-muted-48">
            Try an order-extraction request, a unit conversion, or a free-form request — then ask it again.
          </p>
        )}
        {thread.msgs.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="max-w-[74%] self-end whitespace-pre-wrap rounded-lg rounded-br-sm bg-primary px-[18px] py-[13px] text-base text-white"
              data-testid="user-message">{m.text}</div>
          ) : m.role === "bot" ? (
            <BotMessage key={i} r={m.r} />
          ) : (
            <div key={i} className="text-bad" data-testid="error">{m.text}</div>
          ),
        )}
        {busy && <div className="font-mono text-[11px] text-muted-48" data-testid="thinking">routing…</div>}
        <div ref={end} />
      </div>
      <form className="sticky bottom-0 flex gap-2.5 border-t border-hairline bg-parchment pb-2 pt-4"
        onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Message Cascade…"
          aria-label="Prompt" data-testid="prompt-input"
          className="h-12 flex-1 rounded-pill border border-hairline bg-canvas px-[22px] text-base outline-none focus:border-primary-focus focus:shadow-[0_0_0_1px_#0071e3]" />
        <Button type="submit" disabled={busy || !input.trim()} data-testid="send">Send</Button>
      </form>
    </section>
  );
}
