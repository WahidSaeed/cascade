import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { API, ask, asUser, num, resetDemo } from "../helpers";

const CSV = path.resolve(__dirname, "..", "..", "..", "demo", "data", "labelled_tickets.csv");

test.beforeAll(async ({ request }) => { await resetDemo(request); });

async function waitForIteration(page: Page, n: number) {
  await expect(page.locator(`[data-testid="iteration-report"][data-iteration="${n}"]`)).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId("run-status")).toHaveText("awaiting feedback", { timeout: 60_000 });
  return num(await page.locator(`[data-testid="iteration-report"][data-iteration="${n}"]`).getByTestId("agreement").innerText());
}

test("S6 from recognized pattern to trained model, with the agent @demo", async ({ page, request }) => {
  test.setTimeout(300_000);
  await page.goto("/");
  await asUser(page, "ava");
  // 1. the bell: a new pattern was recognized, with projected savings
  await page.getByTestId("bell").click();
  const note = page.getByTestId("notification").filter({ hasText: "Ticket triage" });
  await expect(note).toContainText("tokens/day");
  await note.click();

  // 2. intake: instructions, the labelled dataset, no integrations
  await expect(page.getByTestId("suggested-plan")).toContainText("rules first");
  await page.getByTestId("intake-instructions").fill("Use these six classes; refund is separate from returns; prefer accuracy over latency");
  await page.getByTestId("dataset-file").setInputFiles(CSV);
  await page.getByTestId("dataset-use").selectOption("both");
  await page.getByTestId("upload-dataset").click();
  await expect(page.getByTestId("dataset-report")).toContainText("300 rows");
  // 3. budget and payback are shown; nothing runs until Start
  await expect(page.getByTestId("payback")).toContainText("pays back");
  await page.getByTestId("start-run").click();

  // 4. iteration 1: a rule, low agreement, recommends ML
  const it1 = await waitForIteration(page, 1);
  expect(it1).toBeLessThan(80);
  await expect(page.locator('[data-iteration="1"]')).toContainText("ML model");
  await page.getByTestId("improve").click();
  // 5. iteration 2: ML, high agreement, with an error table
  const it2 = await waitForIteration(page, 2);
  expect(it2).toBeGreaterThanOrEqual(85);
  await expect(page.locator('[data-iteration="2"]').getByTestId("model-card")).toContainText("ml");
  // 6. keep improving with the refund/returns feedback
  await page.getByTestId("feedback").fill("Refund means the customer wants money back; returns means sending the item back");
  await page.getByTestId("improve").click();
  const it3 = await waitForIteration(page, 3);
  expect(it3).toBeGreaterThanOrEqual(90);
  expect(it3).toBeGreaterThanOrEqual(it2);
  // 7. accept -> verification gate -> Eli approves -> deployed
  await page.getByTestId("accept").click();
  await expect(page.getByTestId("accepted")).toBeVisible({ timeout: 60_000 });
  await asUser(page, "eli");
  await page.goto("/patterns/");
  await page.getByTestId("pattern-card").filter({ hasText: "Ticket triage" }).click();
  await expect(page.getByTestId("model-card")).toBeVisible();
  await page.getByTestId("approve").click();
  await expect(page.getByTestId("pattern-card").filter({ hasText: "Ticket triage" }).getByTestId("pattern-status"))
    .toHaveText("approved", { timeout: 30_000 });
  // 8. a new ticket in the Console is classified by the artifact in milliseconds
  await page.goto("/");
  await asUser(page, "ava");
  const r = await ask(page, "Classify this support ticket: 'The buckle on the Summit boots cracked on day one'");
  await expect(r.getByTestId("route-badge")).toHaveAttribute("data-route", "artifact");
  await expect(r.getByTestId("answer")).toHaveText("technical");
  const p = (await (await request.get(`${API}/api/patterns`)).json()).find((x: { name: string }) => x.name === "Ticket triage");
  expect(p.kind).toBe("ml");
});
