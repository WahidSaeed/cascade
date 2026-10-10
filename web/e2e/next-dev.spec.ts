import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";
import { expect, test } from "@playwright/test";
import { API, resetDemo } from "./helpers";

// P2-01: `web/` runs with `next dev` against the local API (the other specs use the static export).
const DEV = "http://127.0.0.1:3182";
let dev: ChildProcess | undefined;

test.beforeAll(async ({ request }) => {
  await resetDemo(request);
  dev = spawn("npx", ["next", "dev", "-p", "3182", "-H", "127.0.0.1"], {
    cwd: path.resolve(__dirname, ".."),
    env: { ...process.env, NEXT_PUBLIC_API_BASE: API },
    stdio: "ignore",
    detached: true,
  });
  for (let i = 0; i < 120; i++) {
    try {
      const r = await fetch(DEV);
      if (r.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((res) => setTimeout(res, 1000));
  }
  throw new Error("next dev did not start");
});

test.afterAll(() => {
  if (dev?.pid) process.kill(-dev.pid);
});

test("@P2-01 next dev serves the themed workspace against the local API", async ({ page }) => {
  test.setTimeout(240_000);
  await page.goto(`${DEV}/savings/`, { timeout: 120_000 });
  await expect(page.locator(".eyebrow").first()).toHaveText("Savings", { timeout: 60_000 });
  await expect(page.locator(".eyebrow").first()).toHaveCSS("color", "rgb(0, 102, 204)");
  await expect(page.getByTestId("tile-spent-value")).not.toHaveText("");      // live data from the local API
  await page.goto(`${DEV}/`);
  await expect(page.locator("nav")).toHaveCSS("background-color", "rgb(0, 0, 0)");
  await page.getByTestId("prompt-input").fill("Convert 4 kg to lb");
  await expect(page.getByTestId("send")).toBeEnabled({ timeout: 60_000 });
  await page.getByTestId("send").click();
  await expect(page.getByTestId("answer").last()).toHaveText("4 kg = 8.82 lb");
});
