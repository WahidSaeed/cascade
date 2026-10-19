"use client";
import { useCallback, useEffect, useRef, useState } from "react";

// Fetch on mount (and when `key` changes), optionally poll, and expose reload(). Data fetched for an earlier key
// (e.g. the previous persona's collections) is never returned for the new one while it loads.
export function useData<T>(fetcher: () => Promise<T>, key: string, intervalMs?: number) {
  const [loaded, setData] = useState<{ key: string; value: T } | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const fetchRef = useRef(fetcher);
  useEffect(() => { fetchRef.current = fetcher; });

  useEffect(() => {
    let alive = true;
    const run = () =>
      fetchRef.current()
        .then((d) => { if (alive) { setData({ key, value: d }); setError(null); } })
        .catch((e: unknown) => { if (alive) setError(e instanceof Error ? e.message : String(e)); });
    void run();
    // polls only while the tab is visible: a background tab never loads the API
    const t = intervalMs ? setInterval(() => { if (!document.hidden) void run(); }, intervalMs) : undefined;
    return () => { alive = false; if (t) clearInterval(t); };
  }, [key, nonce, intervalMs]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const data = loaded && loaded.key === key ? loaded.value : undefined;
  return { data, error, reload };
}
