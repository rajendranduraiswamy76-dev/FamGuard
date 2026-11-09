import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import type { MemberInput } from "../src/lib/types";
import { API, json, me, mockApi } from "./fixtures";

test("admin manages users and changes survive a page reload", async ({ page }) => {
  await mockApi(page);
  let roster = me.members.map((m) => ({ ...m }));
  await page.route(`${API}/api/me`, (route) => json(route, { ...me, members: roster }));
  await page.route(`${API}/api/members**`, async (route) => {
    const request = route.request();
    if (request.method() === "OPTIONS") return route.fallback();
    const id = new URL(request.url()).pathname.split("/")[3];
    if (request.method() === "POST") {
      const body: MemberInput = request.postDataJSON();
      const member = {
        id: "m-new", display_name: body.display_name, email: body.email ?? null,
        role: body.role ?? "dependent", has_login: false, created_at: "2026-01-01T00:00:00Z",
        relationship: body.relationship ?? null,
      };
      roster.push(member);
      return json(route, member, 201);
    }
    if (request.method() === "PUT") {
      const body: MemberInput = request.postDataJSON();
      roster = roster.map((m) => m.id === id ? {
        ...m, display_name: body.display_name, email: body.email ?? null,
        role: body.role ?? "dependent",
        relationship: body.relationship ?? null,
      } : m);
      return json(route, roster.find((m) => m.id === id));
    }
    if (request.method() === "DELETE") {
      roster = roster.filter((m) => m.id !== id);
      return route.fulfill({ status: 204, headers: { "Access-Control-Allow-Origin": "*" } });
    }
    return route.fallback();
  });

  await page.goto("/");
  await page.getByRole("link", { name: "Users & Categories management", exact: true }).click();
  await expect(page).toHaveURL(/\/users$/);
  await expect(page.getByRole("button", { name: "Delete Ann" })).toBeDisabled();
  await page.getByRole("button", { name: "New user" }).click();
  await page.getByRole("region", { name: "New user", exact: true }).getByLabel("Display name", { exact: true }).fill("Child");
  await page.getByLabel("Relationship", { exact: true }).selectOption("child");
  await page.getByRole("button", { name: "Save user" }).click();
  await expect(page.getByRole("status")).toHaveText("Child created.");
  await page.reload();
  await page.getByRole("button", { name: "Edit Child" }).click();
  await expect(page.getByLabel("Relationship", { exact: true })).toHaveValue("child");
  const edit = page.getByRole("region", { name: "Edit user", exact: true });
  await edit.getByLabel("Display name", { exact: true }).fill("Charlie");
  await edit.getByLabel("Role").selectOption("member");
  await edit.getByLabel("Email").fill("charlie@example.com");
  await page.getByRole("button", { name: "Save user" }).click();
  await expect(page.getByRole("status")).toHaveText("Charlie updated.");
  await page.reload();
  await expect(page.getByRole("button", { name: "Edit Charlie" })).toBeVisible();

  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);

  await page.getByRole("button", { name: "Delete Charlie" }).click();
  await page.getByRole("button", { name: "Confirm delete" }).click();
  await expect(page.getByRole("status")).toHaveText("Charlie deleted.");
  await page.reload();
  await expect(page.getByRole("table", { name: "Family users" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit Charlie" })).toHaveCount(0);
});

test("a non-admin cannot manage users", async ({ page }) => {
  await mockApi(page);
  await page.route(`${API}/api/me`, (route) => json(route, {
    ...me, member: { ...me.member, role: "member" },
  }));
  await page.goto("/users");
  await expect(page.getByRole("main").getByRole("alert")).toHaveText("Only family admins can manage users.");
  await expect(page.getByRole("link", { name: "Users & Categories management", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "New user" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Main", exact: true }).getByRole("link").last())
    .toHaveText("Service health");
});

test("admin adds categories from Users & Categories management", async ({ page }) => {
  await mockApi(page);
  await page.route(`${API}/api/vault/categories`, async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    const body = route.request().postDataJSON();
    return json(route, { id: "c-custom", ...body, is_baseline: false }, 201);
  });
  await page.goto("/users");
  await expect(page.getByRole("heading", { name: "Users & Categories management" })).toBeVisible();
  await page.getByLabel("Category display name", { exact: true }).fill("Vehicle Docs");
  await page.getByRole("button", { name: "Add category" }).click();
  await expect(page.getByRole("list", { name: "Available categories" })).toContainText("vehicle-docs");
  await expect(page.getByRole("status")).toContainText("Added");
});
