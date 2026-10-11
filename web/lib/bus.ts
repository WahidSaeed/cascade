// A tiny in-page signal: the editor tells the explorer (and anything else listening) that patterns changed.
const target = typeof window !== "undefined" ? new EventTarget() : null;

export function emitPatternsChanged(): void {
  target?.dispatchEvent(new Event("patterns-changed"));
}

export function onPatternsChanged(cb: () => void): () => void {
  target?.addEventListener("patterns-changed", cb);
  return () => target?.removeEventListener("patterns-changed", cb);
}
