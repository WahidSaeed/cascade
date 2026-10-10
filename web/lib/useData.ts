"use client";
import { useCallback, useEffect, useRef, useState } from "react";

// Fetch on mount (and when `key` changes), optionally poll, and expose reload().
export function useData<T>(fetcher: () => Promise<T>, key: string, intervalMs?: number) {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const fetchRef = useRef(fetcher);
  useEffect(() => { fetchRef.current = fetcher; });

  useEffect(() => {
    let alive = true;
    const run = () =>
      fetchRef.current()
        .then((d) => { if (alive) { setData(d); setError(null); } })
        .catch((e: unknown) => { if (alive) setError(e instanceof Error ? e.message : String(e)); });
    void run();
    const t = intervalMs ? setInterval(() => void run(), intervalMs) : undefined;
    return () => { alive = false; if (t) clearInterval(t); };
  }, [key, nonce, intervalMs]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, reload };
}
