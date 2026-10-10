"use client";
import { useState } from "react";
import { API_BASE, api } from "@/lib/api";
import { currentUserId } from "@/lib/user";

// Document upload: presigned URL (S3) or the local upload endpoint, then register; ingestion runs async.
// Uploading with an existing document's title creates a new version of that document.
export async function uploadFile(collectionId: string, file: File, title?: string): Promise<void> {
  const up = await api<{ url: string; method: string; key: string }>("/api/documents/upload-url", {
    method: "POST", json: { collection_id: collectionId, filename: file.name } });
  const target = up.url.startsWith("/") ? `${API_BASE}${up.url}` : up.url;
  const put = await fetch(target, { method: "PUT", body: file, headers: { "X-User-Id": currentUserId() } });
  if (!put.ok) throw new Error(`upload failed (${put.status})`);
  const t = title ?? file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  await api("/api/documents", { method: "POST", json: { collection_id: collectionId, title: t, filename: file.name, key: up.key } });
}

export function UploadDropzone({ collectionId, onUploaded }: { collectionId: string; onUploaded: () => void }) {
  const [over, setOver] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function upload(file: File) {
    setMsg(`Uploading ${file.name}…`);
    try {
      await uploadFile(collectionId, file);
      setMsg(`${file.name}: queued for ingestion`);
      onUploaded();
    } catch (e) {
      setMsg(String(e instanceof Error ? e.message : e));
    }
  }

  return (
    <label onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) void upload(f); }}
      className={`flex cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border border-dashed p-6 text-center text-[14px] ${over ? "border-primary bg-primary/5" : "border-hairline bg-pearl"}`}
      data-testid="dropzone">
      <span>Drop a PDF, DOCX, HTML, Markdown, CSV or text file — or click to choose.</span>
      <span className="text-[12px] text-muted-48">A file with the same title becomes a new version.</span>
      <input type="file" className="hidden" data-testid="file-input"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
      {msg && <span className="mt-1 font-mono text-[11px] text-muted-80" data-testid="upload-msg">{msg}</span>}
    </label>
  );
}
