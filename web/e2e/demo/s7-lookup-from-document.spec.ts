import { expect, test } from "@playwright/test";
import { ask, asUser, resetDemo } from "../helpers";

test.beforeAll(async ({ request }) => { await resetDemo(request); });

test("S7 lookup from a document @demo", async ({ page }) => {
  await page.goto("/");
  await asUser(page, "dana");
  await page.getByTestId("pick-all").click();
  const r = await ask(page, "How many vacation days do employees in France get?");
  await expect(r.getByTestId("route-badge")).toHaveAttribute("data-route", "artifact");
  await expect(r.getByTestId("answer")).toContainText("25");
  await expect(r.getByTestId("citation-chip").first()).toContainText("Employee Handbook");
  const ms = Number((await r.getByTestId("latency").innerText()).replace(/[^\d.]/g, ""));
  expect(ms).toBeLessThan(500);
  await page.goto("/patterns/");
  await page.getByTestId("pattern-card").filter({ hasText: "Vacation days by country" }).click();
  await expect(page.getByTestId("kb-dependencies")).toContainText("Employee Handbook");
  await expect(page.getByTestId("kb-dependencies")).toContainText("v1");
});
