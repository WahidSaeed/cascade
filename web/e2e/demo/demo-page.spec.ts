import { expect, test } from "@playwright/test";
import { API, resetDemo } from "../helpers";

test.beforeAll(async ({ request }) => { await resetDemo(request); });

test("Demo page: run-of-show cards, personas, Run step, Reset @demo", async ({ page, request }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Demo" }).click();
  await expect(page).toHaveURL(/\/demo\/$/);
  await expect(page.getByTestId("demo-state")).toContainText("seed phase5-v2");

  // the cards follow the run-of-show: S1 → S2 → S7 → S4 → S5 → S6 → S3, then the spares
  const ids = await page.getByTestId("scenario-card").evaluateAll((cards) => cards.map((c) => c.getAttribute("data-scenario")));
  expect(ids).toEqual(["S1", "S2", "S7", "S4", "S5", "S6", "S3", "S8", "S9"]);
  for (const id of ids) {
    await expect(page.locator(`[data-scenario="${id}"]`).getByTestId("talking-points").locator("li").first()).toBeVisible();
  }

  // the persona button switches the user everywhere (the top-bar switcher follows)
  await page.getByTestId("persona-S4").click();
  await expect(page.getByTestId("user-switcher")).toHaveValue("dana");

  // Run step: replay traffic adds simulated requests to the ledger, with progress on the page
  const before = (await (await request.get(`${API}/api/ledger/summary`)).json()).requests;
  await page.getByTestId("run-replay").click();
  await expect(page.getByTestId("result-replay")).toContainText("started: 40");
  await expect(page.getByTestId("demo-state")).toContainText("replay 40/40 done", { timeout: 60_000 });
  const after = (await (await request.get(`${API}/api/ledger/summary`)).json()).requests;
  expect(after).toBe(before + 40);

  // Run step: discovery (the nightly run) and Chen's Handbook v2 upload (S5's first step)
  const s6 = page.locator('[data-scenario="S6"]');
  await s6.getByTestId("run-discover").click();
  await expect(s6.getByTestId("result-discover")).toBeVisible({ timeout: 60_000 });
  await expect(s6.getByTestId("result-discover")).not.toHaveClass(/text-bad/);
  const s5 = page.locator('[data-scenario="S5"]');
  await s5.getByTestId("run-upload-v2").click();
  await expect(s5.getByTestId("result-upload-v2")).toContainText("version: 2", { timeout: 120_000 });
  const docs = await (await request.get(`${API}/api/documents?collection_id=col_handbook`, { headers: { "X-User-Id": "chen" } })).json();
  expect(docs.find((d: { title: string }) => d.title === "Employee Handbook").version).toBe(2);

  // Reset returns to the starting state
  await page.getByTestId("demo-reset").click();
  await expect(page.getByTestId("demo-reset-status")).toHaveText("Back to the starting state.", { timeout: 120_000 });
  expect((await (await request.get(`${API}/api/ledger/summary`)).json()).requests).toBe(before);
  const restored = await (await request.get(`${API}/api/documents?collection_id=col_handbook`, { headers: { "X-User-Id": "chen" } })).json();
  expect(restored.find((d: { title: string }) => d.title === "Employee Handbook").version).toBe(1);
});
