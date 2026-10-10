import { expect, test } from "@playwright/test";
import { API, EXTRACT, ask, asUser, resetDemo } from "../helpers";

test.beforeAll(async ({ request }) => { await resetDemo(request); });

test("S8 drift protection @demo", async ({ page, request }) => {
  await page.goto("/");
  await asUser(page, "ava");
  const before = await ask(page, EXTRACT.replace("NP-48213", "NP-48214"));
  await expect(before.getByTestId("route-badge")).toHaveAttribute("data-route", "artifact");

  // 1. a faulty build of the extraction artifact is swapped in (it misreads amounts)
  expect((await request.post(`${API}/api/demo/inject-faulty`)).ok()).toBeTruthy();
  // 2. the drift check samples it against the LLM: agreement drops, it's demoted to shadow
  const report = await (await request.post(`${API}/api/demo/drift-check`)).json();
  const extraction = report.find((r: { name: string }) => r.name === "Order details extraction");
  expect(extraction.demoted).toBeTruthy();

  // traffic goes back to the LLM
  const after = await ask(page, EXTRACT.replace("NP-48213", "NP-48215"));
  await expect(after.getByTestId("route-badge")).toHaveAttribute("data-route", "llm");
  await expect(after.getByTestId("answer")).toContainText('"amount": "84.90"');

  // 3. the pattern card shows an alert; the drift cost appears as overhead
  await page.goto("/patterns/");
  const card = page.getByTestId("pattern-card").filter({ hasText: "Order details extraction" });
  await expect(card.getByTestId("pattern-status")).toHaveText("shadow");
  await expect(card.getByTestId("pattern-alert")).toContainText("Drift");
  await card.click();
  await expect(page.getByTestId("drift")).toContainText("rolling agreement");
  const s = await (await request.get(`${API}/api/ledger/summary`)).json();
  expect(s.overhead_by_category.drift.cost_usd).toBeGreaterThan(0);
  await expect(page.getByTestId("bell-count")).toBeVisible();
});
