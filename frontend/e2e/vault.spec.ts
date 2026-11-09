import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

import { API, json, mockApi } from "./fixtures";

test("uses the renamed tabs, places service health last and keeps every page light blue", async ({ page }, testInfo) => {
  test.setTimeout(60000);
  await mockApi(page);
  const tabs = [
    ["Family Vault Query", "/"],
    ["Uploads", "/uploads"],
    ["Income & Expenses Data", "/financial-data"],
    ["Finance Dashboard", "/finance"],
    ["Approval Reviews", "/reviews"],
    ["Users & Categories management", "/users"],
    ["Service health", "/health"],
  ];
  await page.goto("/");
  const navigation = page.getByRole("navigation", { name: "Main", exact: true });
  await expect(navigation.getByRole("link")).toHaveText(tabs.map(([label]) => label));
  for (const [label, path] of tabs) {
    await navigation.getByRole("link", { name: label, exact: true }).click();
    await expect(page).toHaveURL(`http://localhost:3100${path}`);
    await expect(navigation.getByRole("link", { name: label, exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.locator("body")).toHaveCSS("background-color", "rgb(230, 242, 255)");
    await expect(page.locator("html")).toHaveCSS("background-color", "rgb(230, 242, 255)");
    await expect(page.locator("header").first()).toHaveCSS("background-color", "rgb(240, 247, 255)");
    await expect(navigation.getByRole("link", { name: label, exact: true })).toHaveCSS("background-color", "rgb(207, 229, 255)");
    const panels = page.locator("main .enterprise-panel, main .bg-panel");
    await expect(panels.first()).toBeVisible();
    for (const panel of await panels.all()) {
      await expect(panel).toHaveCSS("background-color", "rgb(240, 247, 255)");
    }
  }
  await navigation.getByRole("link", { name: "Family Vault Query", exact: true }).click();
  await expect(navigation.getByRole("link", { name: "Uploads", exact: true })).toHaveCSS("background-color", "rgb(230, 242, 255)");
  await page.screenshot({ path: testInfo.outputPath("light-blue-panels.png"), fullPage: true });
});

test.describe("dashboard", () => {
  test("keeps summary cards compact and aligned with the assistant across screen sizes", async ({ page }, testInfo) => {
    await mockApi(page);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    const expiry = page.getByRole("region", { name: "Expiring ≤ 30 days", exact: true });
    const reviews = page.getByRole("region", { name: "Pending reviews", exact: true });
    const assistant = page.getByRole("region", { name: "Vault assistant", exact: true });
    const upcoming = page.getByRole("region", { name: "Upcoming expirations", exact: true });
    await expect(expiry).toContainText("1 within 7 days");
    const expiryBox = (await expiry.boundingBox())!;
    const reviewsBox = (await reviews.boundingBox())!;
    const assistantBox = (await assistant.boundingBox())!;
    const upcomingBox = (await upcoming.boundingBox())!;
    expect(Math.abs(expiryBox.y - reviewsBox.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(expiryBox.height - reviewsBox.height)).toBeLessThanOrEqual(1);
    expect(Math.abs(expiryBox.width - reviewsBox.width)).toBeLessThanOrEqual(1);
    expect(expiryBox.width).toBeLessThan(260);
    expect(Math.abs(expiryBox.y - assistantBox.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(expiryBox.x - upcomingBox.x)).toBeLessThanOrEqual(1);
    expect(assistantBox.width).toBeGreaterThan(upcomingBox.width);
    await page.screenshot({ path: testInfo.outputPath("dashboard-aligned-desktop.png"), fullPage: true });

    for (const width of [768, 390, 320]) {
      await page.setViewportSize({ width, height: 900 });
      const left = (await expiry.boundingBox())!;
      const right = (await reviews.boundingBox())!;
      expect(Math.abs(left.width - right.width)).toBeLessThanOrEqual(1);
      expect(Math.abs(left.height - right.height)).toBeLessThanOrEqual(1);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      expect((await assistant.boundingBox())!.y).toBeGreaterThan((await upcoming.boundingBox())!.y);
    }
    await page.screenshot({ path: testInfo.outputPath("dashboard-aligned-mobile.png"), fullPage: true });
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
  });

  test("shows metrics and urgent expirations without service health", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");

    await expect(page.getByRole("heading", { name: "Family vault" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Documents", exact: true })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Pinecone vectors" })).toHaveCount(0);
    const urgent = page.getByRole("region", { name: "Upcoming expirations" }).getByRole("listitem").first();
    await expect(urgent).toHaveAttribute("data-urgent", "true");
    await expect(urgent).toContainText("in 3 days");
    await expect(page.getByRole("region", { name: "Service health" })).toHaveCount(0);
    await expect(page.getByText("Smith Family · Ann (admin)")).toBeVisible();
    await expect(page.getByRole("region", { name: "Expiring in the next 1 year" })).toContainText("Annual Insurance");
  });

  test("downloads the income/expense Excel template", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    await expect(page.getByText("Smith Family · Ann (admin)")).toBeVisible();
    await page.getByRole("link", { name: "Income & Expenses Data", exact: true }).click();
    await expect(page).toHaveURL(/\/financial-data$/);

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download sample Excel" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe("famguard-income-expense.xlsx");
    const workbook = await readFile((await download.path())!);
    expect(workbook.subarray(0, 2).toString()).toBe("PK");
  });

  test("has no serious accessibility violations", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    await expect(page.getByRole("region", { name: "Pending reviews", exact: true })).toContainText("1");
    const results = await new AxeBuilder({ page }).analyze();
    const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    const report = serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
    expect(report).toEqual([]);
  });
});

test("uses a pale-blue/navy enterprise theme and fits mobile navigation", async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/uploads");
  const drop = page.getByRole("button", { name: "Drop documents here, or click to choose" });
  await expect(drop).toBeVisible();
  await expect(page.locator("body")).toHaveCSS("background-color", "rgb(230, 242, 255)");
  await expect(page.locator("body")).toHaveCSS("color", "rgb(15, 23, 42)");
  const color = await page.getByRole("button", { name: "Upload documents", exact: true }).evaluate((element) => {
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d")!;
    context.fillStyle = getComputedStyle(element).backgroundColor;
    context.fillRect(0, 0, 1, 1);
    return Array.from(context.getImageData(0, 0, 1, 1).data);
  });
  expect(color.slice(0, 3)).toEqual([29, 78, 216]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
});

test("shows finance month views, upcoming bills and date corrections on mobile", async ({ page }, testInfo) => {
  test.setTimeout(60000);
  await mockApi(page);
  let dated = false;
  const entry = {
    id: "d-old", person_name: "Ann", category: "income", subcategory: "Salary",
    amount: "900.00", currency: "SGD", details: "Earlier salary",
    transaction_date: null, payment_due_date: null, is_paid: true,
  };
  await page.route(`${API}/api/vault/financial/dashboard`, (route) => json(route, {
    today: "2026-11-09",
    months: [
      { month: "2026-10", view: "past", currency: "SGD", income: "900.00", expense: "200.00", net: "700.00", sample_months: 0 },
      { month: "2026-11", view: "current", currency: "SGD", income: "1250.50", expense: "45.99", net: "1204.51", sample_months: 0 },
      { month: "2026-12", view: "projected", currency: "SGD", income: "900.00", expense: "200.00", net: "700.00", sample_months: 1 },
      { month: "2026-12", view: "projected", currency: "USD", income: null, expense: null, net: null, sample_months: 0 },
    ],
    upcoming_bills: [{
      ...entry, id: "d-bill", category: "expense", subcategory: "Rent",
      details: "December rent", amount: "500.00", payment_due_date: "2026-12-01", is_paid: false,
    }],
    expected_cashflows: [{
      source_id: "old-salary", person_name: "Ann", category: "income", subcategory: "Salary",
      details: "Expected monthly salary", amount: "900.00", currency: "SGD",
      payment_due_date: "2026-11-25", previous_due_date: "2026-10-25",
    }, {
      source_id: "old-rent", person_name: "Ann", category: "expense", subcategory: "Rent",
      details: "Expected monthly rent", amount: "500.00", currency: "NOK",
      payment_due_date: "2026-12-01", previous_due_date: "2026-10-01",
    }],
    undated: dated ? [] : [entry], undated_count: dated ? 0 : 1,
  }));
  await page.route(`${API}/api/vault/financial/entries/d-old/date`, (route) => {
    if (route.request().method() !== "PUT") return route.fallback();
    expect(route.request().postDataJSON()).toEqual({ transaction_date: "2026-10-15" });
    dated = true;
    return json(route, { ...entry, transaction_date: "2026-10-15" });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("link", { name: "Finance Dashboard", exact: true }).click();
  await expect(page).toHaveURL(/\/finance$/);
  await expect(page.getByRole("heading", { name: "Finance dashboard", exact: true })).toBeVisible();
  const monthly = page.getByRole("region", { name: "Monthly finance", exact: true });
  await expect(monthly).toContainText("SGD 1,250.50");
  await page.getByLabel("Month view", { exact: true }).selectOption("past");
  await expect(page.getByLabel("Chart month", { exact: true })).toHaveValue("2026-10");
  await expect(monthly).toContainText("SGD 900.00");
  await page.getByLabel("Month view", { exact: true }).selectOption("projected");
  await expect(monthly).toContainText("Estimate (1 months)");
  await monthly.getByText("View detailed monthly totals").click();
  await expect(monthly.getByRole("region", { name: "Monthly totals table", exact: true }).getByText("Insufficient history")).toHaveCount(3);
  await expect(monthly.getByRole("img")).toHaveAttribute("aria-label", /Income 81.8%, expenses 18.2%/);
  await page.getByLabel("Chart currency", { exact: true }).selectOption("USD");
  await expect(monthly.getByRole("img")).toHaveAttribute("aria-label", "2026-12 USD: Insufficient history");
  await page.getByLabel("Chart currency", { exact: true }).selectOption("SGD");
  const bills = page.getByRole("region", { name: "Upcoming bill payments", exact: true });
  await expect(bills).toContainText("December rent");
  await expect(bills).toContainText("Due 2026-12-01");
  await expect(page.getByRole("region", { name: "Expected income", exact: true })).toContainText("Expected 2026-11-25");
  const expectedPayments = page.getByRole("list", { name: "Payment schedule", exact: true });
  await expect(expectedPayments).toContainText("NOK 500.00");
  await expect(expectedPayments).toContainText("Previous due/day 2026-10-01");
  await expect(expectedPayments).toContainText("Recorded unpaid");
  await expect(expectedPayments).toContainText("Estimated");
  const undated = page.getByRole("region", { name: "Undated financial entries", exact: true });
  await expect(undated).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  for (const width of [1440, 320, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await page.evaluate(() => window.scrollTo(0, 0));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await expect(monthly.getByRole("img")).toBeVisible();
    await expect(expectedPayments).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`finance-${width}.png`), fullPage: true });
  }
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
  await page.getByLabel("Transaction date for d-old", { exact: true }).fill("2026-10-15");
  await page.getByRole("button", { name: "Save date for d-old", exact: true }).click();
  await expect(undated).toHaveCount(0);
});

test("shows service health in its own tab", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("link", { name: "Service health", exact: true }).click();
  await expect(page).toHaveURL(/\/health$/);
  await expect(page.getByRole("link", { name: "Service health", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { name: "Service health", level: 1 })).toBeVisible();
  for (const service of ["postgres", "redis", "pinecone"]) {
    await expect(page.getByTestId(`health-${service}`)).toHaveAttribute("data-state", "ok");
  }
  await expect(page.getByRole("region", { name: "Documents", exact: true })).toContainText("12");
  await expect(page.getByRole("region", { name: "Pinecone vectors" })).toContainText("31");
  await expect(page.getByRole("region", { name: "Documents by category" })).toBeVisible();
});

test.describe("uploads", () => {
  test("saves income and expense table rows with currency and uploads Excel", async ({ page }, testInfo) => {
    await mockApi(page);
    await page.goto("/financial-data");
    const financial = page.getByRole("region", { name: "Income/expense data upload", exact: true });
    await expect(financial).toBeVisible();
    await expect(page.getByText("Bulk CSV import", { exact: true })).toHaveCount(0);
    await financial.getByLabel("Person row 1", { exact: true }).selectOption("John");
    await financial.getByLabel("Subcategory row 1", { exact: true }).fill("Salary");
    await financial.getByLabel("Amount row 1", { exact: true }).fill("1250.50");
    await financial.getByLabel("Currency row 1", { exact: true }).fill("SGD");
    await financial.getByLabel("Details row 1", { exact: true }).fill("Monthly salary");
    await financial.getByLabel("Transaction date row 1", { exact: true }).fill("2026-11-01");
    await financial.getByRole("button", { name: "Add row", exact: true }).click();
    await financial.getByLabel("Category row 2", { exact: true }).selectOption("expense");
    await financial.getByLabel("Subcategory row 2", { exact: true }).fill("School supplies");
    await financial.getByLabel("Amount row 2", { exact: true }).fill("45.99");
    await financial.getByLabel("Currency row 2", { exact: true }).fill("NOK");
    await financial.getByLabel("Details row 2", { exact: true }).fill("Weekly grocery shop");
    await financial.getByLabel("Payment due date row 2", { exact: true }).fill("2026-11-20");
    await financial.getByLabel("Paid row 2", { exact: true }).selectOption("no");
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(page.locator("header").first()).toBeInViewport();
    await page.screenshot({ path: testInfo.outputPath("financial-desktop.png"), fullPage: true });
    const requestPromise = page.waitForRequest((request) =>
      request.method() === "POST" && request.url().endsWith("/api/vault/financial/entries"));
    await financial.getByRole("button", { name: "Save table entries", exact: true }).click();
    const request = await requestPromise;
    expect(request.postDataJSON()).toMatchObject({ rows: [
      { person_name: "John", category: "income", subcategory: "Salary", amount: "1250.50", currency: "SGD", transaction_date: "2026-11-01", is_paid: true },
      { category: "expense", subcategory: "School supplies", amount: "45.99", currency: "NOK", payment_due_date: "2026-11-20", is_paid: false },
    ] });
    await expect(financial.getByRole("status")).toContainText("2 saved");
    await financial.getByLabel("Choose income/expense Excel", { exact: true }).setInputFiles({
      name: "filled.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      buffer: Buffer.from("PK synthetic"),
    });
    const uploadPromise = page.waitForRequest((request) =>
      request.method() === "POST" && request.url().endsWith("/api/vault/financial/excel"));
    await financial.getByRole("button", { name: "Upload Excel data", exact: true }).click();
    expect((await uploadPromise).postData()).toContain('filename="filled.xlsx"');
    await expect(financial.getByRole("status")).toContainText("2 saved");
    await expect(page.getByRole("region", { name: "Financial maintenance", exact: true })).toBeVisible();
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
  });

  test("deletes a document from Maintenance after confirmation", async ({ page }) => {
    await mockApi(page);
    let deleted = false;
    await page.route(`${API}/api/vault/uploads?**`, (route) => route.fulfill({
      contentType: "application/json",
      headers: { "Access-Control-Allow-Origin": "*" },
      body: JSON.stringify(deleted ? [] : [{
        thread_id: "saved-1", document_id: "saved-doc", family_member_id: "m-ann",
        status: "completed", filename: "saved.pdf", category: "miscellaneous",
        document_name: "Saved policy", expiration_date: null, requires_alert: false,
      }]),
    }));
    await page.route(`${API}/api/vault/documents/saved-doc`, (route) => {
      if (route.request().method() === "OPTIONS") {
        return route.fulfill({ status: 204, headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Headers": "Authorization",
          "Access-Control-Allow-Methods": "DELETE",
        } });
      }
      deleted = true;
      return route.fulfill({ status: 204, headers: { "Access-Control-Allow-Origin": "*" } });
    });
    await page.goto("/uploads");
    const maintenance = page.getByRole("region", { name: "Maintenance", exact: true });
    await maintenance.getByLabel("Filter by document owner", { exact: true }).selectOption("m-ann");
    await maintenance.getByRole("button", { name: "Delete saved.pdf" }).click();
    await maintenance.getByRole("button", { name: "Cancel", exact: true }).click();
    expect(deleted).toBe(false);
    await maintenance.getByRole("button", { name: "Delete saved.pdf" }).click();
    await maintenance.getByRole("button", { name: "Confirm delete" }).click();
    await expect(maintenance.getByRole("status")).toHaveText("Deleted saved.pdf.");
    await expect(maintenance.getByText("Saved policy", { exact: true })).toHaveCount(0);
    await page.reload();
    await expect(maintenance.getByText("Select an owner or category filter to show uploaded documents.")).toBeVisible();
    await maintenance.getByLabel("Filter by document owner", { exact: true }).selectOption("m-ann");
    await expect(maintenance.getByText("No uploaded documents match the selected filters.")).toBeVisible();
  });

  test("keeps upload history across refresh and shows indexing status", async ({ page }) => {
    await mockApi(page);
    let completed = false;
    await page.route(`${API}/api/vault/uploads?**`, (route) => route.fulfill({
      contentType: "application/json",
      headers: { "Access-Control-Allow-Origin": "*" },
      body: JSON.stringify([{
        thread_id: "saved-1", document_id: "saved-doc", family_member_id: "m-ann",
        status: completed ? "completed" : "processing",
        filename: "saved.pdf", category: "miscellaneous", document_name: "Saved policy",
        expiration_date: null, requires_alert: false,
      }]),
    }));
    await page.goto("/uploads");
    await page.getByLabel("Filter by document owner", { exact: true }).selectOption("m-ann");
    await expect(page.getByText("Processing - not searchable yet", { exact: true })).toBeVisible();
    completed = true;
    await page.getByRole("button", { name: "Refresh uploads" }).click();
    await expect(page.getByText("Indexed - ready to search", { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText("Saved policy", { exact: true })).toHaveCount(0);
    await page.getByLabel("Filter by document owner", { exact: true }).selectOption("m-ann");
    await expect(page.getByText("Saved policy", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Search vault" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Open original saved.pdf" })).toBeVisible();
  });

  test("uploads a document from the dedicated tab", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    await page.getByRole("link", { name: "Uploads", exact: true }).click();
    await expect(page.getByRole("link", { name: "Uploads", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("region", { name: "Custom categories" })).toHaveCount(0);
    await page.getByLabel("Category", { exact: true }).selectOption("identitycards");
    await page.getByLabel("Document owner", { exact: true }).selectOption("m-john");
    await page.getByLabel("Document details", { exact: true }).fill("Travel renewal reference TRAVEL-42");
    const categoryBox = await page.getByLabel("Category", { exact: true }).boundingBox();
    const detailsBox = await page.getByLabel("Document details", { exact: true }).boundingBox();
    expect(categoryBox && detailsBox && detailsBox.y > categoryBox.y + categoryBox.height).toBeTruthy();
    await page.getByLabel("Choose documents").setInputFiles({
      name: "id.jpg", mimeType: "image/jpeg", buffer: Buffer.from("image"),
    });
    const requestPromise = page.waitForRequest((request) =>
      request.method() === "POST" && request.url().endsWith("/api/vault/upload"));
    await page.getByRole("button", { name: "Upload documents", exact: true }).click();
    const request = await requestPromise;
    expect(request.postData()).toContain('name="family_member_id"\r\n\r\nm-john');
    expect(request.postData()).toContain('name="source"\r\n\r\nupload');
    expect(request.postData()).toContain('name="category"\r\n\r\nidentitycards');
    expect(request.postData()).toContain('name="document_details"\r\n\r\nTravel renewal reference TRAVEL-42');
    await expect(page.getByRole("status")).toContainText("accepted for processing");
    await expect(page.getByLabel("Document details", { exact: true })).toHaveValue("");
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
  });

  test("filters maintenance documents by owner and category", async ({ page }) => {
    await mockApi(page);
    await page.route(`${API}/api/vault/uploads?**`, (route) => {
      const params = new URL(route.request().url()).searchParams;
      const selected = params.get("family_member_id") === "m-john"
        && params.get("category") === "identitycards";
      return route.fulfill({
        contentType: "application/json",
        headers: { "Access-Control-Allow-Origin": "*" },
        body: JSON.stringify(selected ? [{
          thread_id: "filtered-1", document_id: "filtered-doc", family_member_id: "m-john",
          status: "completed", filename: "cover.pdf", category: "identitycards",
          document_name: "John cover", document_details: "Annual cover reference MED-42",
          expiration_date: null, requires_alert: false,
        }] : []),
      });
    });
    await page.goto("/uploads");
    const maintenance = page.getByRole("region", { name: "Maintenance", exact: true });
    await expect(maintenance.getByText("Recent documents", { exact: true })).toHaveCount(0);
    await maintenance.getByLabel("Filter by document owner", { exact: true }).selectOption("m-john");
    await maintenance.getByLabel("Filter by category", { exact: true }).selectOption("identitycards");
    await expect(maintenance.getByText("John cover", { exact: true })).toBeVisible();
    await expect(maintenance.getByText(/Annual cover reference MED-42/)).toBeVisible();
    await expect(maintenance.getByRole("button", { name: "Open original cover.pdf" })).toBeEnabled();
    await maintenance.getByRole("button", { name: "Delete cover.pdf" }).click();
    await expect(maintenance.getByLabel("Filter by category", { exact: true })).toBeDisabled();
    await maintenance.getByRole("button", { name: "Cancel", exact: true }).click();
    await maintenance.getByLabel("Filter by category", { exact: true }).selectOption("miscellaneous");
    await expect(maintenance.getByText("No uploaded documents match the selected filters.")).toBeVisible();
    await expect(maintenance.getByText("John cover", { exact: true })).toHaveCount(0);
  });

  test("opens a completed document for date and OCR correction", async ({ page }) => {
    let status = "completed";
    await mockApi(page);
    const uploaded = {
      thread_id: "saved-1", document_id: "saved-doc", family_member_id: "m-ann",
      status, filename: "saved-passport.pdf", category: "identitycards",
      document_name: "Saved passport", expiration_date: "2030-01-01",
      requires_alert: true, document_details: "",
    };
    await page.route(`${API}/api/vault/uploads?**`, (route) => route.fulfill({
      contentType: "application/json", headers: { "Access-Control-Allow-Origin": "*" },
      body: JSON.stringify([{ ...uploaded, status }]),
    }));
    await page.route(`${API}/api/vault/documents/saved-doc/review`, (route) => {
      if (route.request().method() === "OPTIONS") {
        return route.fulfill({ status: 204, headers: {
          "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Authorization",
          "Access-Control-Allow-Methods": "POST",
        } });
      }
      status = "review_required";
      return route.fulfill({
        contentType: "application/json", headers: { "Access-Control-Allow-Origin": "*" },
        body: JSON.stringify({ ...uploaded, status, thread_id: "review-new" }),
      });
    });
    await page.goto("/uploads");
    const maintenance = page.getByRole("region", { name: "Maintenance", exact: true });
    await maintenance.getByLabel("Filter by document owner", { exact: true }).selectOption("m-ann");
    await maintenance.getByRole("button", { name: "Review extraction saved-passport.pdf" }).click();
    await expect(maintenance.getByRole("region", { name: "Confirm extraction review" }))
      .toContainText("Removes existing search entries");
    await maintenance.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(maintenance.getByText("Indexed - ready to search", { exact: true })).toBeVisible();
    await maintenance.getByRole("button", { name: "Review extraction saved-passport.pdf" }).click();
    await maintenance.getByRole("button", { name: "Confirm review", exact: true }).click();
    await expect(maintenance.getByRole("link", { name: "Open Reviews", exact: true })).toBeVisible();
    await expect(maintenance.getByText("Needs approval - not searchable yet", { exact: true })).toBeVisible();
    await expect(maintenance.getByRole("button", { name: "Delete saved-passport.pdf" })).toBeDisabled();
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
  });
});

test.describe("chat agent", () => {
  test("streams an answer with the context filter banner", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    const input = page.getByLabel("Message");
    await input.fill("When does John's passport expire?");
    await input.press("Enter");

    await expect(page.getByTestId("context-filter")).toHaveText("Context Filtered: John | identitycards");
    await expect(page.getByTestId("assistant-text")).toHaveText("John's passport expires 2031-05-04.");
    await expect(page.getByRole("list", { name: "Sources" })).toContainText("John Passport");
  });

  test("uploads a dropped file and links to review", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    await expect(page.getByText("Smith Family · Ann (admin)")).toBeVisible();

    const dataTransfer = await page.evaluateHandle(() => {
      const dt = new DataTransfer();
      dt.items.add(new File(["fake image"], "id.jpg", { type: "image/jpeg" }));
      return dt;
    });
    await page.getByRole("region", { name: "Vault assistant" }).dispatchEvent("drop", { dataTransfer });

    const attachments = page.getByRole("list", { name: "Attachments" });
    await expect(attachments).toContainText("Needs review", { timeout: 15_000 });
    await attachments.getByRole("link", { name: "Review" }).click();
    await expect(page).toHaveURL(/\/reviews$/);
  });
});

test.describe("reviews", () => {
  test("approves a held document with a corrected expiry date", async ({ page }) => {
    let resolved: Record<string, unknown> | null = null;
    await mockApi(page, { onResolve: (body) => (resolved = body) });
    await page.goto("/reviews");

    const card = page.getByRole("article", { name: "Review john-id.jpg" });
    await expect(card).toContainText("Expiry date unreadable");
    await card.getByRole("button", { name: "Approve" }).click();
    await expect(card.getByRole("alert")).toContainText("need an expiry date");

    await card.getByLabel("Expiry date").fill("2030-06-30");
    await card.getByRole("button", { name: "Approve" }).click();
    await expect(page.getByRole("status")).toContainText("Approved john-id.jpg");
    await expect(page.getByText(/nothing needs review/i)).toBeVisible();
    expect(resolved).toMatchObject({
      thread_id: "ing-1",
      decision: "approve",
      corrected_metadata: { expiration_date: "2030-06-30" },
    });
  });
});
