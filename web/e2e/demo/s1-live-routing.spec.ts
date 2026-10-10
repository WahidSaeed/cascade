import { expect, test } from "@playwright/test";
import { EXTRACT, ask, asUser, resetDemo } from "../helpers";

test.beforeAll(async ({ request }) => { await resetDemo(request); });

test("S1 live routing: fast path versus the LLM @demo", async ({ page }) => {
  await page.goto("/");
  await asUser(page, "ava");

  // 1. extraction -> artifact, tens of milliseconds, "saved ~N tokens"
  const a = await ask(page, EXTRACT);
  await expect(a.getByTestId("route-badge")).toHaveAttribute("data-route", "artifact");
  await expect(a.getByTestId("token-note")).toContainText(/saved ~[\d,]+ tokens/);
  await expect(a.getByTestId("answer")).toContainText('"order_number": "NP-48213"');
  const ms = Number((await a.getByTestId("latency").innerText()).replace(/[^\d.]/g, ""));
  expect(ms).toBeLessThan(500);

  // 2. open-ended -> LLM, tokens and cost spent
  const b = await ask(page, "Draft a friendly reply to a customer whose tent pole broke");
  await expect(b.getByTestId("route-badge")).toHaveAttribute("data-route", "llm");
  await expect(b.getByTestId("token-note")).toContainText(/[\d,]+ tokens · \$0\.\d{4}/);

  // 3. the exact repeat of step 1 -> cache
  const c = await ask(page, EXTRACT);
  await expect(c.getByTestId("route-badge")).toHaveAttribute("data-route", "cache");

  // the "why" panel names the light-layer stage that decided
  await a.getByTestId("why-toggle").click();
  await expect(a.getByTestId("why-panel")).toContainText("Pattern recognizer");
  await c.getByTestId("why-toggle").click();
  await expect(c.getByTestId("why-panel")).toContainText("Exact cache");
  await b.getByTestId("why-toggle").click();
  await expect(b.getByTestId("why-panel")).toContainText("heavy LLM");
});
