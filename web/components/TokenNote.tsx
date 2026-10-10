import type { ChatResponse } from "@/lib/types";
import { fmtInt, fmtUsd } from "@/lib/format";

// "LLM · 1,240 tokens · $0.0062" / "saved ~1,180 tokens" / context vs question split.
export function TokenNote({ r }: { r: ChatResponse }) {
  const t = r.tokens;
  if (r.route === "llm") {
    const total = (t.input_tokens ?? 0) + (t.output_tokens ?? 0);
    return (
      <span data-testid="token-note">
        {fmtInt(total)} tokens · {fmtUsd(t.cost_actual_usd, 4)}
        {(t.context_tokens ?? 0) > 0 && (
          <> · context {fmtInt(t.context_tokens)} / question {fmtInt(t.prompt_tokens)}</>
        )}
        {(t.history_tokens ?? 0) > 0 && <> · history {fmtInt(t.history_tokens)}</>}
      </span>
    );
  }
  const est = t.estimate_method && t.estimate_method !== "cache_exact" ? " (est.)" : "";
  return (
    <span data-testid="token-note">
      saved ~{fmtInt(t.tokens_avoided)} tokens · {fmtUsd(t.cost_avoided_usd, 4)}{est}
    </span>
  );
}
