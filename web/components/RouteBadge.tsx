import type { Route } from "@/lib/types";

const LABEL: Record<Route, string> = { cache: "cache", artifact: "artifact", llm: "llm" };
const CLS: Record<Route, string> = { cache: "badge-cache", artifact: "badge-fast", llm: "badge-heavy" };

export function RouteBadge({ route }: { route: Route }) {
  return <span className={`badge ${CLS[route]}`} data-testid="route-badge" data-route={route}>{LABEL[route]}</span>;
}
