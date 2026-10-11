import { expect, test } from "@playwright/test";
import { API, EXTRACT, ask, asUser, resetDemo } from "../helpers";

test.beforeAll(async ({ request }) => { await resetDemo(request); });

test("S8 drift protection @demo", async ({ page, request }) => {
  await page.goto("/");
  await asUser(page, "ava");
  const before = await ask(page, EXTRACT.replace("NP-48213", "NP-48214"));
  await expect(before.getByTestId("route-badge")).toHaveAttribute("data-route", "artifact");

  // 1. on the /demo page, "Inject faulty extraction artifact" swaps in a build that misreads amounts
  await page.goto("/demo/");
  const card = page.locator('[data-scenario="S8"]');
  await card.getByTestId("run-inject-faulty").click();
  await expect(card.getByTestId("result-inject-faulty")).toContainText("injected: true");
  // 2. "Run drift check" samples it against the LLM: agreement drops, it's demoted to shadow
  await card.getByTestId("run-drift-check").click();
  await expect(card.getByTestId("result-drift-check")).toContainText("Order details extraction", { timeout: 120_000 });
  const p0 = (await (await request.get(`${API}/api/patterns`)).json())
    .find((x: { name: string }) => x.name === "Order details extraction");
  expect(p0.status).toBe("shadow");

  // traffic goes back to the LLM
  await page.goto("/");
  const after = await ask(page, EXTRACT.replace("NP-48213", "NP-48215"));
  await expect(after.getByTestId("route-badge")).toHaveAttribute("data-route", "llm");
  await expect(after.getByTestId("answer")).toContainText('"amount": "84.90"');

  // 3. the pattern card shows an alert; the drift cost appears as overhead
  await page.goto("/patterns/");
  const pcard = page.getByTestId("pattern-card").filter({ hasText: "Order details extraction" });
  await expect(pcard.getByTestId("pattern-status")).toHaveText("shadow");
  await expect(pcard.getByTestId("pattern-alert")).toContainText("Drift");
  await pcard.click();
  await expect(page.getByTestId("drift")).toContainText("rolling agreement");
  const s = await (await request.get(`${API}/api/ledger/summary`)).json();
  expect(s.overhead_by_category.drift.cost_usd).toBeGreaterThan(0);
  await expect(page.getByTestId("bell-count")).toBeVisible();
});
