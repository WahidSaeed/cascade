// The demo run-of-show (BUILD.md §17.3, demo/SCRIPT.md): scenario cards in order, with the persona to switch
// to, the prompts to ask, the talking points, and the scripted "Run step" actions.

export type DemoAction = { id: string; label: string; path: string; hint: string };
export type Scenario = {
  id: string;
  title: string;
  minutes: number;
  persona: string;
  personaNote?: string;
  prompts: string[];
  points: string[];
  actions?: DemoAction[];
  spare?: boolean;
};

export const ACTIONS: Record<string, DemoAction> = {
  replay: { id: "replay", label: "Replay traffic", path: "/api/demo/replay-traffic?n=40",
    hint: "Routes 40 prompts from the generated week through the real router (labelled simulated)." },
  uploadV2: { id: "upload-v2", label: "Upload Handbook v2", path: "/api/demo/upload-handbook-v2",
    hint: "Chen uploads the new handbook: Germany goes from 30 to 32 vacation days." },
  rebuild: { id: "rebuild-lookup", label: "Rebuild lookup from v2", path: "/api/demo/rebuild-lookup",
    hint: "A real agent run rebuilds the vacation lookup from the current handbook; verified, then approved by Eli." },
  inject: { id: "inject-faulty", label: "Inject faulty extraction artifact", path: "/api/demo/inject-faulty",
    hint: "Swaps the deployed extraction artifact for a build that misreads amounts." },
  drift: { id: "drift-check", label: "Run drift check", path: "/api/demo/drift-check",
    hint: "Samples live traffic against the LLM; low agreement demotes the artifact to shadow." },
  discover: { id: "discover", label: "Run discovery", path: "/api/demo/discover",
    hint: "The nightly discovery run: clusters recent LLM traffic into recognized patterns." },
};

export const SCENARIOS: Scenario[] = [
  { id: "S1", title: "Live routing: fast path versus the LLM", minutes: 2, persona: "ava",
    prompts: ["Extract the order number, email and amount: 'Order NP-48213 for jane@example.com was charged €84.90 twice'",
      "Draft a friendly reply to a customer whose tent pole broke",
      "(repeat the extraction prompt)"],
    points: ["Most prompts are the same few tasks with different values: compiled into small verified programs, no tokens.",
      "Everything else still goes to the LLM, unchanged — the why panel shows which stage decided.",
      "Every answer carries its token cost or the tokens it avoided."] },
  { id: "S2", title: "Safety rails", minutes: 1.5, persona: "ava", personaNote: "Dana for the follow-up",
    prompts: ["Convert 5 km to miles", "Convert 50 km to miles", "What's the status of today's shipments?",
      "(Dana) How many vacation days in France?", "(Dana) and for Spain?", "Rate the urgency 1-5: 'hmm'"],
    points: ["Slots must match exactly: the 5 km answer never serves 50 km.",
      "Time-sensitive and follow-up prompts never take a shortcut.",
      "An unsure artifact says so and the LLM answers — wrong-but-fast never ships."] },
  { id: "S7", title: "Lookup artifact built from a document", minutes: 0.5, persona: "dana",
    prompts: ["How many vacation days do employees in France get?"],
    points: ["Some knowledge questions are table lookups: answered in milliseconds, with the handbook citation.",
      "The model card lists its kb_dependencies. Run this before S5."] },
  { id: "S4", title: "Knowledge answers, shared safely", minutes: 2, persona: "dana",
    personaNote: "then Ava, Dana, Ben",
    prompts: ["How many weeks of parental leave do we offer?", "(Ava) same question → cache",
      "(Dana) What's the bonus range for team leads?", "(Ben) same question → cited answer"],
    points: ["Cached knowledge answers are keyed to their sources: shared only between people who can read them.",
      "Access control runs in the retrieval query: Dana never sees a compensation snippet."] },
  { id: "S5", title: "Documents change, answers stay correct", minutes: 1.5, persona: "chen",
    personaNote: "then Dana",
    prompts: ["(Dana) How many vacation days do employees in Germany get?"],
    points: ["Upload v2: the cache misses and the answer is 32, citing v2.",
      "The lookup built from v1 is re-verified and demoted automatically; one click rebuilds it from v2."],
    actions: [ACTIONS.uploadV2, ACTIONS.rebuild] },
  { id: "S6", title: "From recognized pattern to trained model", minutes: 4, persona: "eli",
    prompts: ["Bell → Ticket triage → intake", "Instructions: Use these six classes; refund is separate from returns; prefer accuracy over latency",
      "Dataset: demo/data/labelled_tickets.csv (both)", "Start → rule → ml → feedback → Accept → Approve",
      "(Ava) Classify this support ticket: 'The buckle on the Summit boots cracked on day one'"],
    points: ["Cascade asks before it spends: instructions, data, integrations, budget and payback.",
      "The agent tries the cheapest thing first and says why it moves on; you steer it in plain language.",
      "Nothing ships without the verification gate and a human approval; agent tokens are booked as overhead."],
    actions: [ACTIONS.discover] },
  { id: "S3", title: "Savings dashboard", minutes: 1, persona: "eli",
    prompts: ["Open Savings"],
    points: ["Savings are net: compile, verification, agent, drift and embedding costs are all subtracted.",
      "Payback per pattern is computed from the ledger, never asserted.",
      "Seeded figures are labelled simulated; make demo-measure produces the real-Bedrock report."],
    actions: [ACTIONS.replay] },
  { id: "S8", title: "Drift protection", minutes: 1, persona: "eli", spare: true,
    prompts: ["Patterns → Order details extraction"],
    points: ["Approved is not forever: live traffic is sampled against the LLM.",
      "Low agreement demotes the artifact to shadow automatically; the drift check shows as overhead."],
    actions: [ACTIONS.inject, ACTIONS.drift] },
  { id: "S9", title: "Integration with an existing system", minutes: 1, persona: "eli", spare: true,
    prompts: ["Priority routing intake → register CRM tier lookup (target: the CRM URL shown above)",
      "Tools → Integrations → Approve"],
    points: ["Integrations need an admin's approval and reach only their declared target.",
      "The artifact is flagged not fully deterministic."] },
];
