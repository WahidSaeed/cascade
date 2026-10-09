import { defineConfig, type ReporterDescription } from "@playwright/test";

// The full local stack for E2E: the API (Demo LLM, local auth, fresh demo-reset state) and the
// static web export. Started by Playwright; see scripts/e2e_stack.sh.
const API = "http://127.0.0.1:8181";
const WEB = "http://127.0.0.1:3181";
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
  webServer: [
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
