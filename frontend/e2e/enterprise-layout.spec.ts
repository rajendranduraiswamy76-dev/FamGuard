import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mockApi } from "./fixtures";

const pages = [
  ["/", "Family vault"],
  ["/uploads", "Uploads"],
  ["/financial-data", "Income & Expenses Data"],
  ["/finance", "Finance dashboard"],
  ["/reviews", "Pending reviews"],
  ["/users", "Users & Categories management"],
  ["/health", "Service health"],
] as const;

test("enterprise pages have consistent headings, contained layouts and accessible controls", async ({ page }, testInfo) => {
  test.setTimeout(120000);
  await mockApi(page);
  for (const width of [1440, 768, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const [path, title] of pages) {
      await page.goto(path);
      const heading = page.getByRole("heading", { name: title, level: 1, exact: true });
      await expect(heading).toBeVisible();
      await expect(heading).toHaveCSS("font-size", width >= 640 ? "30px" : "24px");
      await expect(heading).toHaveCSS("color", "rgb(15, 23, 42)");
      await expect(page.locator("body")).toHaveCSS("background-color", "rgb(230, 242, 255)");
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      const panels = page.locator(".enterprise-panel");
      await expect(panels.first()).toBeVisible();
      for (const panel of await panels.all()) {
        await expect(panel).toHaveCSS("border-radius", "12px");
        await expect(panel).toHaveCSS("background-color", "rgb(240, 247, 255)");
      }
      if (width === 1440) {
        const results = await new AxeBuilder({ page }).analyze();
        expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
      }
      if (width !== 768) {
        await page.screenshot({ path: testInfo.outputPath(`enterprise-${path.replace(/\//g, "") || "query"}-${width}.png`), fullPage: true });
      }
    }
  }
});

test("upload objects align in desktop columns and the skip link moves focus to content", async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/uploads");
  const owner = page.getByLabel("Document owner", { exact: true });
  const category = page.getByLabel("Category", { exact: true });
  const details = page.getByLabel("Document details", { exact: true });
  const ownerBox = (await owner.boundingBox())!;
  const categoryBox = (await category.boundingBox())!;
  const detailsBox = (await details.boundingBox())!;
  const dropBox = (await page.getByRole("button", { name: "Drop documents here, or click to choose" }).boundingBox())!;
  expect(ownerBox.x).toBe(categoryBox.x);
  expect(ownerBox.width).toBe(categoryBox.width);
  expect(detailsBox.x).toBe(ownerBox.x);
  expect(detailsBox.y).toBeGreaterThan(categoryBox.y + categoryBox.height);
  expect(dropBox.x).toBeGreaterThan(ownerBox.x + ownerBox.width);
  await expect(owner).toHaveCSS("background-color", "rgb(255, 255, 255)");
  await expect(owner).toHaveCSS("min-height", "40px");
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main")).toBeFocused();
});
