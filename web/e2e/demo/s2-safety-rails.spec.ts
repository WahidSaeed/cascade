import { expect, test } from "@playwright/test";
import { ask, asUser, resetDemo } from "../helpers";

test.beforeAll(async ({ request }) => { await resetDemo(request); });

test("S2 safety rails @demo", async ({ page }) => {
  await page.goto("/");
  await asUser(page, "ava");

  // slots differ -> never the cached answer for another value
  const five = await ask(page, "Convert 5 km to miles");
  await expect(five.getByTestId("answer")).toHaveText("5 km = 3.11 miles");
  const fifty = await ask(page, "Convert 50 km to miles");
  await expect(fifty.getByTestId("route-badge")).not.toHaveAttribute("data-route", "cache");
  await expect(fifty.getByTestId("answer")).toHaveText("50 km = 31.07 miles");

  // time-sensitive -> not cached
  await ask(page, "What's the status of today's shipments?");
  const again = await ask(page, "What's the status of today's shipments?");
  await expect(again.getByTestId("route-badge")).toHaveAttribute("data-route", "llm");
  await again.getByTestId("why-toggle").click();
  await expect(again.getByTestId("why-panel")).toContainText("time-sensitive");

  // follow-up -> skips caches and artifacts, answered with the conversation history
  await asUser(page, "dana");
  await ask(page, "How many vacation days in France?");
  const fu = await ask(page, "and for Spain?");
  await expect(fu.getByTestId("route-badge")).toHaveAttribute("data-route", "llm");
  await expect(fu.getByTestId("token-note")).toContainText("history");
  await fu.getByTestId("why-toggle").click();
  await expect(fu.getByTestId("why-panel")).toContainText("Follow-up");
});
