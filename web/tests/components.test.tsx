import { render, screen } from "@testing-library/react";
import { RouteBadge } from "@/components/RouteBadge";
import { RouteMix } from "@/components/RouteMix";
import { StatTile } from "@/components/StatTile";
import { TokenNote } from "@/components/TokenNote";
import { fmtInt, fmtMs, fmtPct, fmtUsd } from "@/lib/format";
import type { ChatResponse } from "@/lib/types";

const base = {
  request_id: "r", conversation_id: "c", answer: "a", latency_ms: 12, confidence: null, pattern_id: null,
  pattern_name: null, artifact_kind: null, model_id: null, sources: [], not_fully_deterministic: false,
  light: { stage_hit: "none", template: "t", slots: {}, latency_ms_by_stage: {} },
} as unknown as ChatResponse;

describe("formatting", () => {
  it("formats numbers, money, percentages and durations", () => {
    expect(fmtInt(1240)).toBe("1,240");
    expect(fmtUsd(0.0062)).toBe("$0.0062");
    expect(fmtUsd(-1.5)).toBe("−$1.50");
    expect(fmtUsd(1234)).toBe("$1,234");
    expect(fmtPct(0.5662)).toBe("56.6%");
    expect(fmtMs(2400)).toBe("2.4 s");
    expect(fmtMs(3.21)).toBe("3.2 ms");
  });
});

describe("RouteBadge", () => {
  it.each([["cache", "badge-cache"], ["artifact", "badge-fast"], ["llm", "badge-heavy"]] as const)(
    "styles the %s route", (route, cls) => {
      render(<RouteBadge route={route} />);
      const b = screen.getByTestId("route-badge");
      expect(b).toHaveTextContent(route);
      expect(b).toHaveClass(cls);
    });
});

describe("TokenNote", () => {
  it("shows tokens and cost spent on the LLM route, with the context split", () => {
    const r = { ...base, route: "llm", tokens: { input_tokens: 1000, output_tokens: 240, cost_actual_usd: 0.0062,
      context_tokens: 800, prompt_tokens: 20, history_tokens: 0 } } as unknown as ChatResponse;
    render(<TokenNote r={r} />);
    expect(screen.getByTestId("token-note")).toHaveTextContent("1,240 tokens · $0.0062 · context 800 / question 20");
  });
  it("shows tokens saved, marking estimates", () => {
    const r = { ...base, route: "artifact", tokens: { tokens_avoided: 1180, cost_avoided_usd: 0.005,
      estimate_method: "pattern_measured" } } as unknown as ChatResponse;
    render(<TokenNote r={r} />);
    expect(screen.getByTestId("token-note")).toHaveTextContent("saved ~1,180 tokens · $0.0050 (est.)");
  });
  it("does not mark real cache usage as an estimate", () => {
    const r = { ...base, route: "cache", tokens: { tokens_avoided: 90, cost_avoided_usd: 0.001,
      estimate_method: "cache_exact" } } as unknown as ChatResponse;
    render(<TokenNote r={r} />);
    expect(screen.getByTestId("token-note")).not.toHaveTextContent("est.");
  });
});

describe("StatTile and RouteMix", () => {
  it("renders a labelled metric and the estimate marker", () => {
    render(<StatTile label="Net saved" value="$1.68" testId="t" estimated note="after overhead" />);
    expect(screen.getByTestId("t-value")).toHaveTextContent("$1.68");
    expect(screen.getByTestId("t")).toHaveTextContent("est.");
  });
  it("splits routes into shares", () => {
    render(<RouteMix byRoute={{ cache: 1, artifact: 1, llm: 2 }} />);
    expect(screen.getByTestId("route-mix-llm")).toHaveTextContent("LLM 2 · 50.0%");
  });
});
