"use client";
import { createContext, useContext, useSyncExternalStore } from "react";

// The active persona (CASCADE_AUTH=local). Kept in localStorage so the API client and every
// view agree on who is asking; storage failures fall back to an in-memory value.
type Ctx = { userId: string; setUserId: (id: string) => void };
const KEY = "cascade.user";
const DEFAULT_USER = "ava";
let memoryUser = DEFAULT_USER;
const listeners = new Set<() => void>();

export function currentUserId(): string {
  if (typeof window === "undefined") return DEFAULT_USER;
  try {
    return window.localStorage.getItem(KEY) || memoryUser;
  } catch {
    return memoryUser;
  }
}

function setCurrentUser(id: string): void {
  memoryUser = id;
  try {
    window.localStorage.setItem(KEY, id);
  } catch {
    /* storage unavailable: keep in memory */
  }
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const UserCtx = createContext<Ctx>({ userId: DEFAULT_USER, setUserId: setCurrentUser });

export function UserProvider({ children }: { children: React.ReactNode }) {
  const userId = useSyncExternalStore(subscribe, currentUserId, () => DEFAULT_USER);
  return <UserCtx.Provider value={{ userId, setUserId: setCurrentUser }}>{children}</UserCtx.Provider>;
}

export const useUser = () => useContext(UserCtx);
