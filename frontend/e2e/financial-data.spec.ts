import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { API, json, mockApi } from "./fixtures";

test("moves financial tools out of Uploads and maintains filtered entries on mobile", async ({ page }, testInfo) => {
  test.setTimeout(60000);
  await mockApi(page);
  let deleted = false;
  let listCalls = 0;
  let entry = {
    id: "f-1", person_name: "Ann", category: "expense", subcategory: "Rent", amount: "100.25",
    currency: "SGD", details: "Original rent", transaction_date: "2026-10-10",
    payment_due_date: "2026-10-20", is_paid: true,
  };
  await page.route(`${API}/api/vault/financial/entries?**`, (route) => {
    listCalls += 1;
    const params = new URL(route.request().url()).searchParams;
    expect(params.get("person_name")).toBe("Ann");
    return json(route, { entries: deleted ? [] : [entry], has_next: false });
  });
  await page.route(`${API}/api/vault/financial/entries/f-1`, (route) => {
    if (route.request().method() !== "PUT") return route.fallback();
    expect(route.request().postDataJSON()).toMatchObject({
      amount: "99.99", details: "Corrected rent", payment_due_date: "2026-11-20",
    });
    entry = { ...entry, amount: "99.99", details: "Corrected rent", payment_due_date: "2026-11-20" };
    return route.fulfill({ status: 204, headers: { "Access-Control-Allow-Origin": "*" } });
  });
  await page.route(`${API}/api/vault/documents/f-1`, (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    deleted = true;
    return route.fulfill({ status: 204, headers: { "Access-Control-Allow-Origin": "*" } });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/uploads");
  await expect(page.getByRole("region", { name: "Income/expense data upload", exact: true })).toHaveCount(0);
  await expect(page.getByText("Select an owner or category filter to show uploaded documents.")).toBeVisible();
  await page.getByRole("link", { name: "Income & Expenses Data", exact: true }).click();
  await expect(page).toHaveURL(/\/financial-data$/);
  await expect(page.getByRole("region", { name: "Income/expense data upload", exact: true })).toBeVisible();
  const maintenance = page.getByRole("region", { name: "Financial maintenance", exact: true });
  await expect(maintenance.getByText("Select a filter to show financial entries.")).toBeVisible();
  expect(listCalls).toBe(0);
  await maintenance.getByLabel("Person", { exact: true }).selectOption("Ann");
  await maintenance.getByLabel("Income/Expense", { exact: true }).selectOption("expense");
  await maintenance.getByLabel("Currency", { exact: true }).selectOption("SGD");
  await maintenance.getByLabel("Paid status", { exact: true }).selectOption("true");
  await maintenance.getByLabel("Subcategory", { exact: true }).fill("Rent");
  await maintenance.getByLabel("Transaction date from", { exact: true }).fill("2026-10-01");
  await maintenance.getByLabel("Transaction date to", { exact: true }).fill("2026-10-31");
  await maintenance.getByLabel("Payment due date from", { exact: true }).fill("2026-10-01");
  await maintenance.getByLabel("Payment due date to", { exact: true }).fill("2026-11-30");
  await expect(maintenance.getByText("Original rent", { exact: true })).toBeVisible();
  await maintenance.getByRole("button", { name: "Edit financial entry f-1", exact: true }).click();
  await expect(maintenance.getByLabel("Person", { exact: true })).toBeDisabled();
  await maintenance.getByLabel("Edit amount", { exact: true }).fill("99.99");
  await maintenance.getByLabel("Edit details", { exact: true }).fill("Corrected rent");
  await maintenance.getByLabel("Edit payment due date", { exact: true }).fill("2026-11-20");
  await page.screenshot({ path: testInfo.outputPath("financial-maintenance-mobile.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
  await maintenance.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(maintenance.getByText("Financial entry updated.")).toBeVisible();
  await maintenance.getByRole("button", { name: "Delete financial entry f-1", exact: true }).click();
  await maintenance.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(deleted).toBe(false);
  await maintenance.getByRole("button", { name: "Delete financial entry f-1", exact: true }).click();
  await maintenance.getByRole("button", { name: "Confirm delete", exact: true }).click();
  await expect(maintenance.getByText("No financial entries match the selected filters.")).toBeVisible();
  await maintenance.getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(maintenance.getByText("Select a filter to show financial entries.")).toBeVisible();
});
