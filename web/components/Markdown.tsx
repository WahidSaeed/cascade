import type { ReactNode } from "react";

// The light Markdown LLM answers use: **bold**, *italic*, `code` and "- " / "* " bullets. Rendered as React
// elements only (never as HTML), so an answer can't inject markup. Line breaks are kept by the caller's
// whitespace-pre-wrap.
const INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\n]+\*)/g;

function inline(text: string, key: string): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    const k = `${key}-${i}`;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) return <b key={k}>{part.slice(2, -2)}</b>;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2)
      return <code key={k} className="rounded-sm bg-divider px-1 font-mono text-[0.9em]">{part.slice(1, -1)}</code>;
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) return <i key={k}>{part.slice(1, -1)}</i>;
    return part;
  });
}

export function Markdown({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <>
      {lines.map((line, i) => {
        const bullet = /^\s*[-*]\s+/.exec(line);
        const body = bullet ? `• ${line.slice(bullet[0].length)}` : line;
        return <span key={i}>{inline(body, String(i))}{i < lines.length - 1 ? "\n" : ""}</span>;
      })}
    </>
  );
}
