import { defineConfig, type ReporterDescription } from "@playwright/test";

// The full local stack for E2E: the API (Demo LLM, local auth, fresh demo-reset state) and the
// static web export, started by Playwright (scripts/e2e_api.sh, scripts/e2e_web.sh).
// PW_STACK=compose runs the same tests against the running docker compose stack instead (make demo-e2e-stack):
// Postgres, the separate agent-worker, the live Demo LLM latency — the stack used on stage.
const COMPOSE = process.env.PW_STACK === "compose";
const API = COMPOSE ? "http://localhost:8080" : "http://127.0.0.1:8181";
const WEB = COMPOSE ? "http://localhost:3000" : "http://127.0.0.1:3181";
const reporters: ReporterDescription[] = [["list"]];
if (process.env.PW_JUNIT) reporters.push(["junit", { outputFile: process.env.PW_JUNIT }]);

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  retries: 1,
  workers: 1,
  fullyParallel: false,
  reporter: reporters,
  use: {
    baseURL: WEB,
    trace: "retain-on-failure",
    video: process.env.PW_RECORD ? "on" : "off",
  },
  webServer: COMPOSE ? [] : [
    {
      command: "bash ../scripts/e2e_api.sh",
      url: `${API}/api/health`,
      timeout: 240_000,
      reuseExistingServer: false,
    },
    {
      command: "bash ../scripts/e2e_web.sh",
      url: WEB,
      timeout: 300_000,
      reuseExistingServer: false,
    },
  ],
});
