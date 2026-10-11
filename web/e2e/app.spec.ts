import { existsSync } from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";
import { API, ask, asUser, num, resetDemo } from "./helpers";

test.beforeAll(async ({ request }) => {
  await resetDemo(request);
});

test("@P2-01 every view renders in the deck theme", async ({ page, request }) => {
  const triage = (await (await request.get(`${API}/api/patterns`)).json())
    .find((p: { name: string }) => p.name === "Ticket triage");
  for (const [route, eyebrow] of [["/", "Console"], ["/savings/", "Savings"], ["/knowledge/", "Knowledge"],
    ["/patterns/", "Patterns"], [`/patterns/intake/?id=${triage.id}`, "Pattern recognized"], ["/tools/", "Tools"],
    ["/tools/integrations/", "Tools · Integrations"], ["/demo/", "Demo"]]) {
    await page.goto(route);
    const eb = page.locator(".eyebrow").first();
    await expect(eb).toHaveText(eyebrow);
    // deck tokens: primary #0066cc eyebrow in JetBrains Mono, black top bar, parchment canvas, Inter body
    await expect(eb).toHaveCSS("color", "rgb(0, 102, 204)");
    expect(await eb.evaluate((e) => getComputedStyle(e).fontFamily)).toMatch(/JetBrains|mono/i);
    await expect(page.locator("nav")).toHaveCSS("background-color", "rgb(0, 0, 0)");
    await expect(page.locator("body")).toHaveCSS("background-color", "rgb(245, 245, 247)");
    expect(await page.locator("body").evaluate((e) => getComputedStyle(e).fontFamily)).toMatch(/Inter/i);
  }
  await page.goto("/");
  await expect(page.getByTestId("demo-llm-badge")).toBeVisible();
  await expect(page.getByTestId("health-dot")).toHaveCSS("background-color", "rgb(26, 143, 60)");
});

test("@P2-02 @P6-02 switching users changes cache behaviour and per-user ledger numbers", async ({ page, request }) => {
  const before = await (await request.get(`${API}/api/ledger/by-user`)).json();
  const rowsBefore = Object.fromEntries(before.map((r: { user_id: string; requests: number }) => [r.user_id, r.requests]));
  // contains PII (an email address), so its cache entry is private to the asking user
  const prompt = "Draft an apology email to omar@example.com about his late Ridgeline tent";
  await page.goto("/");
  await asUser(page, "ava");
  await expect(await ask(page, prompt)).toContainText("llm");
  await expect((await ask(page, prompt)).getByTestId("route-badge")).toHaveAttribute("data-route", "cache");
  await asUser(page, "ben");
  await expect((await ask(page, prompt)).getByTestId("route-badge")).toHaveAttribute("data-route", "llm");
  await asUser(page, "ava");
  await expect(page.getByTestId("bot-message")).toHaveCount(2);     // each user keeps their own conversation

  await page.goto("/savings/");
  const ava = page.getByTestId("user-row-ava");
  const ben = page.getByTestId("user-row-ben");
  await expect(ava.locator("td").nth(1)).toHaveText(String((rowsBefore.ava ?? 0) + 2).replace(/\B(?=(\d{3})+(?!\d))/g, ","));
  await expect(ben.locator("td").nth(1)).toHaveText(String((rowsBefore.ben ?? 0) + 1).replace(/\B(?=(\d{3})+(?!\d))/g, ","));
});

test("@P2-03 the Savings view reconciles with /api/ledger/summary", async ({ page, request }) => {
  await page.goto("/savings/");
  await expect(page.getByTestId("tile-spent-value")).not.toHaveText("");
  const s = await (await request.get(`${API}/api/ledger/summary`)).json();
  expect(num(await page.getByTestId("tile-spent-value").innerText())).toBe(s.tokens_spent);
  expect(num(await page.getByTestId("tile-avoided-value").innerText())).toBe(s.tokens_avoided);
  expect(num(await page.getByTestId("tile-reduction-value").innerText())).toBeCloseTo(s.token_reduction * 100, 1);
  expect(num(await page.getByTestId("tile-net-value").innerText())).toBeCloseTo(s.net_saved_usd, 2);
  expect(num(await page.getByTestId("tile-rate-value").innerText())).toBeCloseTo(s.savings_rate * 100, 1);
  for (const r of ["cache", "artifact", "llm"]) {
    expect(await page.getByTestId(`route-mix-${r}`).innerText()).toContain((s.by_route[r] ?? 0).toLocaleString("en-US"));
  }
  const byUser = await (await request.get(`${API}/api/ledger/by-user`)).json();
  expect(byUser.reduce((a: number, u: { requests: number }) => a + u.requests, 0)).toBe(s.requests);
  await expect(page.getByTestId("simulated-label")).toBeVisible();
});

test("@P2-04 @P6-03 the static export works", async ({ page }) => {
  const out = path.resolve(__dirname, "..", "out");
  for (const f of ["index.html", "savings/index.html", "patterns/index.html", "tools/index.html"]) {
    expect(existsSync(path.join(out, f)), f).toBeTruthy();
  }
  // deep links load straight from the exported files (S3 + CloudFront stand-in)
  await page.goto("/tools/");
  await expect(page.getByTestId("cfg-cache_threshold")).toHaveValue("0.95");
  await page.goto("/patterns/");
  await expect(page.getByTestId("pattern-card").first()).toBeVisible();
});

test("patterns: review drawer shows code, verification and approve state", async ({ page }) => {
  await page.goto("/patterns/");
  await page.getByTestId("pattern-card").filter({ hasText: "Order details extraction" }).click();
  const drawer = page.getByTestId("pattern-drawer");
  await expect(drawer).toBeVisible();
  await expect(drawer.getByTestId("code-view")).toContainText("def handle");
  await expect(drawer.getByTestId("verification")).toContainText("passed");
  await expect(drawer.getByTestId("approve")).toHaveCount(0);          // already approved
});

test("tools: a threshold change is saved and persisted", async ({ page, request }) => {
  await page.goto("/tools/");
  await page.getByTestId("cfg-cache_threshold").fill("0.96");
  await page.getByTestId("save-config").click();
  await expect(page.getByTestId("config-msg")).toHaveText("Saved.");
  const cfg = await (await request.get(`${API}/api/config`)).json();
  expect(cfg.values.cache_threshold).toBe(0.96);
  await resetDemo(request);
  expect((await (await request.get(`${API}/api/config`)).json()).values.cache_threshold).toBe(0.95);
});
