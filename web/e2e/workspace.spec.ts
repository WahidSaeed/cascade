import { expect, test, type Page } from "@playwright/test";
import { API, ask, asUser, resetDemo } from "./helpers";

test.beforeAll(async ({ request }) => { await resetDemo(request); });
test.afterAll(async ({ request }) => { await resetDemo(request); });

/** Replace the open Monaco model's text (the same path as typing: it fires the editor's change event). */
async function setCode(page: Page, edit: (code: string) => string) {
  await expect.poll(() => page.evaluate(() => !!(window as unknown as { monaco?: unknown }).monaco)).toBe(true);
  await page.evaluate((src) => {
    const m = (window as unknown as { monaco: { editor: { getEditors(): { getModel(): { getValue(): string; setValue(v: string): void } }[] } } }).monaco;
    const model = m.editor.getEditors()[0].getModel();
    model.setValue(new Function("code", `return (${src})(code)`)(model.getValue()) as string);
  }, edit.toString());
}

async function openPattern(page: Page, name: string) {
  await page.goto("/patterns/");
  await page.getByTestId("pattern-card").filter({ hasText: name }).click();
  await expect(page.getByTestId("artifact-editor")).toBeVisible();
}

test("workspace shell: activity bar, explorer, editor area and status bar", async ({ page }) => {
  await page.goto("/patterns/");
  for (const name of ["Console", "Patterns", "Knowledge", "Savings", "Tools"]) {
    await expect(page.getByTestId("activity-bar").getByRole("link", { name })).toBeVisible();
  }
  await expect(page.getByTestId("side-panel").getByTestId("pattern-explorer")).toBeVisible();
  await expect(page.getByTestId("status-bar")).toContainText("llm: demo");
  await expect(page.getByTestId("breadcrumb")).toContainText("patterns");
  await page.getByTestId("activity-bar").getByRole("link", { name: "Savings" }).click();
  await expect(page.getByTestId("side-panel")).toHaveCount(0);
  await expect(page.locator(".eyebrow").first()).toHaveText("Savings");
});

test("rule artifact: test a prompt, edit the code, save, verify and approve", async ({ page }) => {
  await page.goto("/");
  await asUser(page, "eli");
  await openPattern(page, "Unit conversion");
  await expect(page.getByTestId("code-view")).toContainText("def handle");
  // Test panel: the saved artifact, out of process
  await page.getByTestId("try-input").fill("Convert 5 km to miles");
  await page.getByTestId("try-run").click();
  await expect(page.getByTestId("try-result")).toContainText("3.11");
  // edit in Monaco: the tab shows unsaved changes, ⌘S / Save writes a new version that needs verification
  await setCode(page, (c) => `# reviewed in the workspace editor\n${c}`);
  await expect(page.getByTestId("editor-tab").filter({ hasText: "handler.py" })).toContainText("●");
  await page.getByTestId("save-code").click();
  const card = page.getByTestId("pattern-card").filter({ hasText: "Unit conversion" });
  await expect(card.getByTestId("pattern-status")).toHaveText("draft", { timeout: 15_000 });
  await expect(page.getByTestId("code-view")).toContainText("reviewed in the workspace editor");
  await page.getByTestId("verify").click();
  await expect(page.getByTestId("verification")).toContainText("passed", { timeout: 60_000 });
  await page.getByTestId("approve").click();
  await expect(card.getByTestId("pattern-status")).toHaveText("approved", { timeout: 30_000 });
});

test("dl artifact: its Python is editable, trains in a sandboxed job, and serves after approval", async ({ page, request }) => {
  test.setTimeout(420_000);
  await page.goto("/");
  await asUser(page, "eli");
  await openPattern(page, "Urgency scoring");
  await expect(page.getByTestId("editor-tab").filter({ hasText: "model.py" })).toBeVisible();
  await expect(page.getByTestId("code-view")).toContainText("BertForSequenceClassification");
  await expect(page.getByTestId("model-card")).toBeVisible();
  // the edit: a stricter input guard (at least four words before the model answers)
  await setCode(page, (c) => c.replace("MIN_WORDS = 3", "MIN_WORDS = 4"));
  await expect(page.getByTestId("editor-tab").filter({ hasText: "model.py" })).toContainText("●");
  await page.getByTestId("train-source").click();
  await expect(page.getByTestId("output-log")).toContainText("train_source", { timeout: 300_000 });
  await expect(page.getByTestId("train-status")).toHaveText("awaiting_feedback", { timeout: 300_000 });
  await page.getByTestId("train-accept").click();
  await expect(page.getByTestId("train-status")).toHaveText("accepted", { timeout: 120_000 });
  const card = page.getByTestId("pattern-card").filter({ hasText: "Urgency scoring" });
  await expect(card.getByTestId("pattern-status")).toHaveText("draft", { timeout: 15_000 });
  await expect(page.getByTestId("code-view")).toContainText("MIN_WORDS = 4");
  await page.getByTestId("approve").click();
  await expect(card.getByTestId("pattern-status")).toHaveText("approved", { timeout: 30_000 });
  // the Test panel runs the new version: three words now defer to the LLM, a full sentence is scored
  await page.getByTestId("panel-test").click();
  await page.getByTestId("try-input").fill("Rate the urgency 1-5: 'boots arrived broken'");
  await page.getByTestId("try-run").click();
  await expect(page.getByTestId("try-result")).toContainText("defers to the LLM");

  await page.goto("/");
  await asUser(page, "ava");
  const r = await ask(page, "Rate the urgency 1-5: 'My trek starts tomorrow and my boots haven't arrived'");
  await expect(r.getByTestId("route-badge")).toHaveAttribute("data-route", "artifact");
  const p = (await (await request.get(`${API}/api/patterns`)).json()).find((x: { name: string }) => x.name === "Urgency scoring");
  expect(p.source_code).toContain("MIN_WORDS = 4");
});
