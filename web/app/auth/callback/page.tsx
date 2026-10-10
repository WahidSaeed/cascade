"use client";
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { completeSignIn } from "@/lib/api";

function Callback() {
  const params = useSearchParams();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const code = params.get("code");
    if (!code) return;
    completeSignIn(code).then(() => router.replace("/")).catch((e: unknown) => setError(String(e)));
  }, [params, router]);
  return <p className="text-muted-48">{error ?? (params.get("code") ? "Signing in…" : "No authorization code.")}</p>;
}

export default function AuthCallbackPage() {
  return <Suspense><Callback /></Suspense>;
}
