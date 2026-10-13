import fs from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { ask, asUser, resetDemo, setCode } from "./helpers";

// The admin control centre: version history with diffs and rollback, usage across users, retraining.
test.beforeAll(async ({ request }) => { await resetDemo(request); });
test.afterAll(async ({ request }) => { await resetDemo(request); });

async function openPattern(page: Page, name: string) {
  await page.goto("/patterns/");
  await page.getByTestId("pattern-card").filter({ hasText: name }).click();
  await expect(page.getByTestId("artifact-editor")).toBeVisible();
}

test.describe.configure({ timeout: 240_000 });

test("history: an edit becomes v2, the diff shows it, rollback restores v1 as v3 and it serves again", async ({ page }) => {
  await page.goto("/");
  await asUser(page, "eli");
  await openPattern(page, "Unit conversion");
  await setCode(page, (c) => c.replace("def handle(prompt", "# broken on purpose\ndef handle_old(prompt"));
  await page.getByTestId("save-code").click();
  await expect(page.getByTestId("save-code")).toHaveCount(0, { timeout: 120_000 });     // the save has finished
  const card = page.getByTestId("pattern-card").filter({ hasText: "Unit conversion" });
  await expect(card.getByTestId("pattern-status")).toHaveText("draft", { timeout: 15_000 });

  await page.getByTestId("panel-history").click();
  const rows = page.getByTestId("version-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText("v2");
  await expect(rows.first()).toContainText("code edited by eli");
  await page.getByTestId("diff-v1").click();
  await expect(page.getByTestId("diff-bar")).toContainText("v1");
  // left = v1 as it was, right = the current code (read from the diff editor's two models)
  const sides = await page.evaluate(() => {
    const m = (window as unknown as { monaco: { editor: { getModels(): { uri: { path: string }; getValue(): string }[] } } }).monaco;
    const models = m.editor.getModels();
    return { v1: models.find((x) => x.uri.path.includes("/v1/handler.py"))?.getValue() ?? "",
      current: models.find((x) => x.uri.path.includes("/diff-current/handler.py"))?.getValue() ?? "" };
  });
  expect(sides.v1).toContain("def handle(prompt");
  expect(sides.v1).not.toContain("broken on purpose");
  expect(sides.current).toContain("broken on purpose");
  await page.getByTestId("diff-close").click();
  await expect(page.getByTestId("diff-bar")).toHaveCount(0);

  await page.getByTestId("rollback-v1").click();
  await page.getByTestId("confirm-rollback-v1").click();
  await expect(page.getByTestId("history-msg")).toContainText("approved and serving", { timeout: 120_000 });
  await expect(card.getByTestId("pattern-status")).toHaveText("approved", { timeout: 15_000 });
  await expect(page.getByTestId("version-row").first()).toContainText("rollback to v1 by eli");
  await expect(page.getByTestId("code-view")).not.toContainText("broken on purpose");

  await page.goto("/");
  await asUser(page, "ben");
  const r = await ask(page, "Convert 12 km to miles");
  await expect(r.getByTestId("route-badge")).toHaveAttribute("data-route", "artifact");
});

test("usage: one artifact, every user it serves", async ({ page }) => {
  await page.goto("/");
  for (const [user, q] of [["ava", "Convert 7 km to miles"], ["chen", "Convert 9 kg to lb"]]) {
    await asUser(page, user);
    const r = await ask(page, q);
    await expect(r.getByTestId("route-badge")).toHaveAttribute("data-route", "artifact");
  }
  await asUser(page, "eli");
  await openPattern(page, "Unit conversion");
  await page.getByTestId("panel-usage").click();
  await expect(page.getByTestId("usage-row").filter({ hasText: "Chen" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("usage-row").filter({ hasText: "Ava" })).toBeVisible();
  expect(Number((await page.getByTestId("usage-artifact").innerText()).replace(/\D/g, ""))).toBeGreaterThanOrEqual(2);
});

test("retrain: a dl artifact trains on its data plus an uploaded file, then waits for review", async ({ page }) => {
  test.setTimeout(900_000);
  await page.goto("/");
  await asUser(page, "eli");
  await openPattern(page, "Urgency scoring");
  const card = page.getByTestId("retrain");
  // 60 more labelled rows on top of the data the current version was trained on
  const rows = fs.readFileSync(path.resolve(__dirname, "../../demo/data/urgency_train.jsonl"), "utf8").split("\n").filter(Boolean);
  const extra = test.info().outputPath("urgency_extra.jsonl");
  fs.writeFileSync(extra, rows.slice(-60).join("\n") + "\n");
  await card.getByTestId("retrain-file").setInputFiles(extra);
  await card.getByTestId("retrain-upload").click();
  await expect(card.getByTestId("retrain-dataset")).toContainText("rows", { timeout: 30_000 });
  await card.getByTestId("retrain-start").click();
  await expect(page.getByTestId("output-log")).toContainText("train_source", { timeout: 600_000 });
  await expect(page.getByTestId("train-status")).toHaveText("awaiting_feedback", { timeout: 600_000 });
  await page.getByTestId("train-accept").click();
  await expect(page.getByTestId("train-status")).toHaveText("accepted", { timeout: 120_000 });
  await page.getByTestId("panel-history").click();
  await expect(page.getByTestId("version-row").first()).toContainText("retrained with more data");
});
