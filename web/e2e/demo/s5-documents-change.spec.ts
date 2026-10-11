import path from "node:path";
import { expect, test } from "@playwright/test";
import { API, ask, asUser, resetDemo } from "../helpers";

const GERMANY = "How many vacation days do employees in Germany get?";

test.beforeAll(async ({ request }) => { await resetDemo(request); });

test("S5 documents change, answers stay correct @demo", async ({ page, request }) => {
  // before: the answer from Handbook v1, then cached
  await page.goto("/");
  await asUser(page, "dana");
  await page.getByTestId("pick-all").click();
  const before = await ask(page, GERMANY);
  await expect(before.getByTestId("answer")).toContainText("30");

  // 1. Chen uploads Handbook v2 from the Knowledge view
  await page.goto("/knowledge/?c=col_handbook");
  await asUser(page, "chen");
  const row = page.getByTestId("document-row").filter({ hasText: "Employee Handbook" });
  await row.getByTestId("new-version-input").setInputFiles(path.resolve(__dirname, "..", "..", "..", "demo", "docs", "handbook_v2.docx"));
  await expect(row.getByTestId("doc-version")).toHaveText("v2", { timeout: 60_000 });
  await expect(row.getByTestId("doc-status")).toHaveText("ready");

  // 2. the cache misses and the new answer cites v2
  await page.goto("/");
  await asUser(page, "dana");
  await page.getByTestId("pick-all").click();
  const after = await ask(page, GERMANY);
  await expect(after.getByTestId("route-badge")).not.toHaveAttribute("data-route", "cache");
  await expect(after.getByTestId("answer")).toContainText("32");
  await expect(after.getByTestId("citation-chip").first()).toContainText("v2");

  // 3. the lookup built from v1 is re-verified, demoted and flagged on its pattern card
  const patterns = await (await request.get(`${API}/api/patterns`)).json();
  const lookup = patterns.find((p: { name: string }) => p.name === "Vacation days by country");
  expect(lookup.status).toBe("shadow");
  await page.goto("/patterns/");
  const card = page.getByTestId("pattern-card").filter({ hasText: "Vacation days by country" });
  await expect(card.getByTestId("pattern-status")).toHaveText("shadow");
  await expect(card.getByTestId("pattern-alert")).toContainText("re-verification");

  // it stays demoted until rebuilt from v2: one click on the /demo page
  await page.goto("/demo/");
  const s5 = page.locator('[data-scenario="S5"]');
  await s5.getByTestId("run-rebuild-lookup").click();
  await expect(s5.getByTestId("result-rebuild-lookup")).toContainText("rebuilt: true", { timeout: 120_000 });
  await expect(s5.getByTestId("result-rebuild-lookup")).toContainText("Employee Handbook v2");
  await page.goto("/");
  await asUser(page, "dana");
  await page.getByTestId("pick-all").click();
  const pt = await ask(page, "How many vacation days do employees in Portugal get?");
  await expect(pt.getByTestId("route-badge")).toHaveAttribute("data-route", "artifact");
  await expect(pt.getByTestId("answer")).toContainText("22");
});
