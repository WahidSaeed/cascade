import path from "node:path";
import { expect, test } from "@playwright/test";
import { API, ask, asUser, resetDemo } from "../helpers";

const CSV = path.resolve(__dirname, "..", "..", "..", "demo", "data", "labelled_tickets.csv");

test.beforeAll(async ({ request }) => { await resetDemo(request); });

test("S9 integration with an existing system @demo", async ({ page, request }) => {
  test.setTimeout(240_000);
  await page.goto("/patterns/");
  await asUser(page, "ben");
  await page.getByTestId("pattern-card").filter({ hasText: "Priority routing" }).click();
  await page.getByTestId("open-intake").click();
  // 1. the intake registers the HTTP integration "CRM tier lookup" (served by crm-mock)
  await page.getByTestId("intake-instructions").fill("go straight to ml");
  await page.getByTestId("dataset-file").setInputFiles(CSV);
  await page.getByTestId("dataset-use").selectOption("train");
  await page.getByTestId("upload-dataset").click();
  await expect(page.getByTestId("dataset-report")).toContainText("300 rows");
  await page.getByTestId("integration-name").fill("CRM tier lookup");
  const crm = (await (await request.get(`${API}/api/demo/state`)).json()).crm_tier_url;   // where crm-mock runs
  await page.getByTestId("integration-target").fill(crm);
  await page.getByTestId("register-integration").click();
  await expect(page.getByTestId("integration-status")).toContainText("pending");
  await page.getByTestId("start-run").click();
  await expect(page.getByTestId("awaiting-start")).toContainText("approved");      // blocked until approval
  const runUrl = page.url();
  // 2. Eli approves the integration
  await asUser(page, "eli");
  await page.goto("/tools/integrations/");
  const row = page.getByTestId("integration-row").filter({ hasText: "CRM tier lookup" });
  await row.getByTestId("approve-integration").click();
  await expect(row.getByTestId("integration-state")).toHaveText("approved");
  // the run starts; Ben accepts; Eli approves the artifact
  await page.goto(runUrl);
  await asUser(page, "ben");
  await page.getByTestId("start-run").click();
  await expect(page.locator('[data-testid="iteration-report"][data-iteration="1"]')).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText("not fully deterministic (uses an integration)")).toBeVisible();
  await page.getByTestId("accept").click();
  await expect(page.getByTestId("accepted")).toBeVisible({ timeout: 60_000 });
  await asUser(page, "eli");
  await page.goto("/patterns/");
  const card = page.getByTestId("pattern-card").filter({ hasText: "Priority routing" });
  await card.click();
  await page.getByTestId("approve").click();
  await expect(card.getByTestId("pattern-status")).toHaveText("approved", { timeout: 30_000 });
  // 3. the artifact uses the customer's tier and is flagged "not fully deterministic"
  await expect(card.getByTestId("not-deterministic")).toBeVisible();
  await page.goto("/");
  await asUser(page, "ava");
  const r = await ask(page, "Which queue should handle this ticket from jane@example.com: 'I was charged twice for my tent order'");
  await expect(r.getByTestId("route-badge")).toHaveAttribute("data-route", "artifact");
  await expect(r.getByTestId("answer")).toHaveText("priority-billing");
  await expect(r).toContainText("not fully deterministic");
});
