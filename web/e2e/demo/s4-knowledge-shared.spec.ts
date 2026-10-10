import { expect, test } from "@playwright/test";
import { ask, asUser, resetDemo } from "../helpers";

const PARENTAL = "How many weeks of parental leave do we offer?";
const BONUS = "What's the bonus range for team leads?";

test.beforeAll(async ({ request }) => { await resetDemo(request); });

async function attachAll(page: import("@playwright/test").Page) {
  await page.getByTestId("pick-all").click();
}

test("S4 knowledge answers, shared safely @demo", async ({ page }) => {
  await page.goto("/");
  await asUser(page, "dana");
  await attachAll(page);
  const d1 = await ask(page, PARENTAL);
  await expect(d1.getByTestId("route-badge")).toHaveAttribute("data-route", "llm");
  await expect(d1.getByTestId("citation-chip").first()).toContainText("Employee Handbook");
  await d1.getByTestId("citation-chip").first().click();
  await expect(d1.getByTestId("citation-snippet")).toContainText("16 weeks");
  await expect(d1.getByTestId("token-note")).toContainText("context");

  await asUser(page, "ava");
  await attachAll(page);
  const a1 = await ask(page, PARENTAL);
  await expect(a1.getByTestId("route-badge")).toHaveAttribute("data-route", "cache");     // same sources: shared
  await expect(a1.getByTestId("answer")).toContainText("16 weeks");

  await asUser(page, "dana");
  const d2 = await ask(page, BONUS);
  await expect(d2.getByTestId("answer")).toHaveText("I couldn't find that in the documents you can access.");
  await expect(d2.getByTestId("citations")).toHaveCount(0);

  await asUser(page, "ben");
  await attachAll(page);
  const b1 = await ask(page, BONUS);
  await expect(b1.getByTestId("answer")).toContainText("8 to 12 percent");
  await expect(b1.getByTestId("citation-chip").first()).toContainText("Compensation Guide");
});
