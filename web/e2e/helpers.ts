import { expect, type APIRequestContext, type Page } from "@playwright/test";

export const API = process.env.PW_STACK === "compose" ? "http://localhost:8080" : "http://127.0.0.1:8181";

export async function resetDemo(request: APIRequestContext) {
  const r = await request.post(`${API}/api/demo/reset`, { timeout: 120_000 });
  expect(r.ok()).toBeTruthy();
}

export async function asUser(page: Page, userId: string) {
  await page.getByTestId("user-switcher").selectOption(userId);
}

/** Send a prompt in the Console and return the newest answer's locator. */
export async function ask(page: Page, prompt: string) {
  const before = await page.getByTestId("bot-message").count();
  await page.getByTestId("prompt-input").fill(prompt);
  await page.getByTestId("send").click();
  await expect(page.getByTestId("bot-message")).toHaveCount(before + 1, { timeout: 30_000 });
  return page.getByTestId("bot-message").nth(before);
}

export const EXTRACT =
  "Extract the order number, email and amount: 'Order NP-48213 for jane@example.com was charged €84.90 twice'";

/** Parse "1,234" / "$1.23" / "12.5%" back into a number. */
export function num(text: string): number {
  const neg = text.includes("−") || text.trim().startsWith("-");
  const v = Number(text.replace(/[^0-9.]/g, ""));
  return neg ? -v : v;
}

/** Replace the open Monaco model's text (the same path as typing: it fires the editor's change event). */
export async function setCode(page: Page, edit: (code: string) => string) {
  await expect.poll(() => page.evaluate(() => !!(window as unknown as { monaco?: unknown }).monaco)).toBe(true);
  await page.evaluate((src) => {
    const m = (window as unknown as { monaco: { editor: { getEditors(): { getModel(): { getValue(): string; setValue(v: string): void } }[] } } }).monaco;
    const model = m.editor.getEditors()[0].getModel();
    model.setValue(new Function("code", `return (${src})(code)`)(model.getValue()) as string);
  }, edit.toString());
}
