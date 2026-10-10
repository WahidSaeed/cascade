import { expect, test } from "@playwright/test";
import { API, num, resetDemo } from "../helpers";

test.beforeAll(async ({ request }) => { await resetDemo(request); });

test("S3 savings dashboard: the seeded week @demo", async ({ page, request }) => {
  await page.goto("/savings/");
  await expect(page.getByTestId("simulated-label")).toHaveText("Demo data — simulated traffic");
  for (const t of ["tile-spent", "tile-avoided", "tile-reduction", "tile-net", "tile-rate"]) {
    await expect(page.getByTestId(t)).toBeVisible();
  }
  expect(num(await page.getByTestId("tile-net-value").innerText())).toBeGreaterThan(0);  // net of overhead
  await expect(page.getByTestId("tile-net")).toContainText("overhead");
  await expect(page.getByTestId("cost-chart").locator(".recharts-area")).toHaveCount(2);
  await expect(page.getByTestId("route-mix")).toContainText("Artifact");
  await expect(page.getByTestId("user-table").locator("tbody tr")).toHaveCount(25);
  // payback is computed from the ledger, never hard-coded
  const byPattern = await (await request.get(`${API}/api/ledger/by-pattern`)).json();
  const extraction = byPattern.find((p: { pattern_name: string }) => p.pattern_name === "Order details extraction");
  expect(extraction.paid_back).toBeTruthy();
  await expect(page.getByTestId("pattern-table")).toContainText(
    `paid back after ${extraction.paid_back_after_requests.toLocaleString("en-US")} requests`);
  await expect(page.getByTestId("input-mix")).toContainText("question");
  await expect(page.getByTestId("estimate-note")).toBeVisible();
});
