// Shapes of API responses. Chat and pattern types come from the generated OpenAPI types;
// the ledger aggregates are plain JSON objects described here.
import type { components } from "./api-types";

export type ChatResponse = components["schemas"]["ChatResponse"];
export type Pattern = components["schemas"]["Pattern"];
export type VerifyResult = components["schemas"]["VerifyResult"];
export type Route = ChatResponse["route"];

export type Summary = {
  requests: number;
  by_route: Partial<Record<Route, number>>;
  input_tokens: number;
  output_tokens: number;
  tokens_spent: number;
  tokens_spent_requests: number;
  overhead_tokens: number;
  tokens_avoided: number;
  context_tokens_avoided: number;
  token_reduction: number;
  actual_cost_usd: number;
  runtime_cost_usd: number;
  gross_saved_usd: number;
  overhead_cost_usd: number;
  overhead_by_category: Record<string, { cost_usd: number; tokens: number }>;
  baseline_cost_usd: number;
  net_saved_usd: number;
  savings_rate: number;
  estimated_share: number;
  estimate_methods: Record<string, number>;
  input_mix: { prompt: number; context: number; history: number; system: number; cache_read: number; cache_write: number };
  uncited: number;
  simulated_requests: number;
};

export type Bucket = {
  bucket: number;
  requests: number;
  actual_cost_usd: number;
  baseline_cost_usd: number;
  gross_saved_usd: number;
  overhead_cost_usd: number;
  net_saved_usd: number;
  tokens_spent: number;
  tokens_avoided: number;
  routes: Record<Route, number>;
};

export type UserRow = {
  user_id: string;
  requests: number;
  tokens_spent: number;
  tokens_avoided: number;
  actual_cost_usd: number;
  gross_saved_usd: number;
  overhead_cost_usd: number;
  net_saved_usd: number;
  served_without_llm: number;
};

export type PatternRow = {
  pattern_id: string;
  pattern_name: string;
  requests: number;
  tokens_avoided: number;
  gross_saved_usd: number;
  runtime_cost_usd: number;
  overhead_cost_usd: number;
  overhead_tokens: number;
  net_saved_usd: number;
  paid_back: boolean;
  paid_back_after_requests: number | null;
};

export type Persona = { id: string; name: string; role: string; groups: string[]; is_admin: boolean };
export type Health = { status: string; db: string; llm: string; auth: string; demo: boolean; artifact_runtime: string };
export type ToolConfig = components["schemas"]["ToolConfig"];
