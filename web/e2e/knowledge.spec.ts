import path from "node:path";
import { expect, test } from "@playwright/test";
import { asUser, resetDemo } from "./helpers";

test.beforeAll(async ({ request }) => { await resetDemo(request); });

test("@P3-02 the Knowledge view only lists collections the user can read", async ({ page }) => {
  await page.goto("/knowledge/");
  await asUser(page, "dana");
  await expect(page.getByTestId("collections").getByRole("button")).toHaveCount(2);
  await expect(page.getByTestId("collections")).not.toContainText("Compensation Guide");
  await asUser(page, "ben");
  await expect(page.getByTestId("collections").getByRole("button")).toHaveCount(4);
  await expect(page.getByTestId("collection-col_compensation").getByTestId("access-badge")).toContainText("group: managers");
});

test("@P3-01 upload a document, watch it become ready, preview its chunks", async ({ page }) => {
  await page.goto("/knowledge/?c=col_products");
  await asUser(page, "ben");
  await page.getByTestId("file-input").setInputFiles({
    name: "care_guide.md", mimeType: "text/markdown",
    buffer: Buffer.from("# Care guide\n\n## Boots\n\nWax Summit boots twice a season.\n"),
  });
  const row = page.getByTestId("document-row").filter({ hasText: "Care Guide" });
  await expect(row.getByTestId("doc-status")).toHaveText("ready", { timeout: 30_000 });
  await row.getByTestId("preview").click();
  await expect(page.getByTestId("chunk-preview")).toContainText("Wax Summit boots");
  // a new version keeps the document and bumps its version
  const v2 = path.resolve(__dirname, "..", "..", "demo", "docs", "product_faq.md");
  await row.getByTestId("new-version-input").setInputFiles(v2);
  await expect(row.getByTestId("doc-version")).toHaveText("v2", { timeout: 30_000 });
});
