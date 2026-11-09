import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { FinancialDataView as UploadsView } from "@/app/components/FinancialDataView";
import { API, apiError, interceptMultipart, jsonResponse, me, server } from "./msw";
import { renderWithApp } from "./render";

async function fillRow(number: number, category = "income", amount = "1500.25") {
  await userEvent.selectOptions(screen.getByLabelText(`Category row ${number}`), category);
  await userEvent.type(screen.getByLabelText(`Subcategory row ${number}`), category === "income" ? "Salary" : "Groceries");
  await userEvent.type(screen.getByLabelText(`Amount row ${number}`), amount);
  await userEvent.clear(screen.getByLabelText(`Currency row ${number}`));
  await userEvent.type(screen.getByLabelText(`Currency row ${number}`), "SGD");
  await userEvent.type(screen.getByLabelText(`Details row ${number}`), "Monthly household entry");
}

describe("Income/expense data upload", () => {
  it("preserves supported family currencies beyond the original popular list", async () => {
    server.use(http.get(`${API}/api/me`, () => HttpResponse.json({
      ...me, family: { ...me.family, currency: "NOK" },
    })));
    renderWithApp(<UploadsView />);
    expect(await screen.findByLabelText("Currency row 1")).toHaveValue("NOK");
    await userEvent.click(screen.getByRole("button", { name: "Add row" }));
    expect(screen.getByLabelText("Currency row 2")).toHaveValue("NOK");
  });

  it("has editable suggestions and submits dates for unpaid bills", async () => {
    let body: unknown;
    server.use(http.post(`${API}/api/vault/financial/entries`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ accepted: 1, rejected: 0, errors: [], errors_truncated: false });
    }));
    renderWithApp(<UploadsView />);
    await screen.findByLabelText("Person row 1");
    await fillRow(1, "expense", "200");
    expect(screen.getByLabelText("Subcategory row 1")).toHaveAttribute("list", "financial-expense-subcategories");
    await userEvent.clear(screen.getByLabelText("Subcategory row 1"));
    await userEvent.type(screen.getByLabelText("Subcategory row 1"), "New custom bill");
    await userEvent.clear(screen.getByLabelText("Currency row 1"));
    await userEvent.type(screen.getByLabelText("Currency row 1"), "NOK");
    await userEvent.selectOptions(screen.getByLabelText("Paid row 1"), "no");
    await userEvent.click(screen.getByRole("button", { name: "Save table entries" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("payment due date");
    await userEvent.type(screen.getByLabelText("Payment due date row 1"), "2026-12-01");
    await userEvent.type(screen.getByLabelText("Transaction date row 1"), "2026-11-09");
    await userEvent.click(screen.getByRole("button", { name: "Save table entries" }));
    await waitFor(() => expect(body).toMatchObject({ rows: [{
      subcategory: "New custom bill", currency: "NOK", is_paid: false,
      transaction_date: "2026-11-09", payment_due_date: "2026-12-01",
    }] }));
  });

  it("renders all table fields, saves multiple rows and clears accepted entries", async () => {
    let body: unknown;
    server.use(http.post(`${API}/api/vault/financial/entries`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ accepted: 2, rejected: 0, errors: [], errors_truncated: false });
    }));
    renderWithApp(<UploadsView />);
    await screen.findByRole("region", { name: "Income/expense data upload" });
    expect(screen.queryByText("Bulk CSV import")).not.toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Currency" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Person row 1"), "John");
    await fillRow(1);
    await userEvent.click(screen.getByRole("button", { name: "Add row" }));
    await fillRow(2, "expense", "25.99");
    await userEvent.click(screen.getByRole("button", { name: "Save table entries" }));
    expect(await screen.findByText(/2 saved/)).toBeInTheDocument();
    expect(body).toEqual({ rows: [
      { person_name: "John", category: "income", subcategory: "Salary", amount: "1500.25",
        currency: "SGD", details: "Monthly household entry", transaction_date: null,
        payment_due_date: null, is_paid: true },
      { person_name: "Ann", category: "expense", subcategory: "Groceries", amount: "25.99",
        currency: "SGD", details: "Monthly household entry", transaction_date: null,
        payment_due_date: null, is_paid: true },
    ] });
    expect(screen.getByLabelText("Amount row 1")).toHaveValue(null);
    expect(screen.queryByLabelText("Amount row 2")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Financial maintenance" })).toBeInTheDocument();
  });

  it("validates required values and preserves entries on server failure", async () => {
    let calls = 0;
    server.use(http.post(`${API}/api/vault/financial/entries`, () => {
      calls += 1;
      return apiError(500, "failure", "Could not save entries");
    }));
    renderWithApp(<UploadsView />);
    await screen.findByRole("button", { name: "Save table entries" });
    await userEvent.click(screen.getByRole("button", { name: "Save table entries" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Row 1:");
    expect(calls).toBe(0);
    await fillRow(1, "income", "-1");
    await userEvent.click(screen.getByRole("button", { name: "Save table entries" }));
    expect(calls).toBe(0);
    await userEvent.clear(screen.getByLabelText("Amount row 1"));
    await userEvent.type(screen.getByLabelText("Amount row 1"), "25.50");
    await userEvent.click(screen.getByRole("button", { name: "Save table entries" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not save entries");
    expect(screen.getByLabelText("Amount row 1")).toHaveValue(25.5);
    expect(screen.getByLabelText("Details row 1")).toHaveValue("Monthly household entry");
  });

  it("keeps rejected table rows only, preventing duplicate accepted rows on retry", async () => {
    server.use(http.post(`${API}/api/vault/financial/entries`, () => HttpResponse.json({
      accepted: 1, rejected: 1, errors: [{ row: 2, reason: "Name must be unique" }],
      errors_truncated: false,
    })));
    renderWithApp(<UploadsView />);
    await screen.findByLabelText("Amount row 1");
    await fillRow(1);
    await userEvent.click(screen.getByRole("button", { name: "Add row" }));
    await fillRow(2, "expense", "17.40");
    await userEvent.click(screen.getByRole("button", { name: "Save table entries" }));
    expect(await screen.findByText("Row 2: Name must be unique")).toBeInTheDocument();
    expect(screen.getByLabelText("Amount row 1")).toHaveValue(17.4);
    expect(screen.queryByLabelText("Amount row 2")).not.toBeInTheDocument();
  });

  it("uploads a workbook to FastAPI and displays row errors", async () => {
    const calls = interceptMultipart("/api/vault/financial/excel", () => jsonResponse({
      accepted: 2, rejected: 1, errors: [{ row: 4, reason: "Unsupported currency" }],
      errors_truncated: false,
    }));
    renderWithApp(<UploadsView />);
    const input = await screen.findByLabelText("Choose income/expense Excel");
    await userEvent.upload(input, new File(["xlsx"], "filled.xlsx"));
    await userEvent.click(screen.getByRole("button", { name: "Upload Excel data" }));
    expect(await screen.findByText("Row 4: Unsupported currency")).toBeInTheDocument();
    expect(calls).toHaveLength(1);
    expect((calls[0].get("file") as File).name).toBe("filled.xlsx");
    expect(screen.getByText(/remove accepted rows before retrying/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload Excel data" })).toBeDisabled();
  });

  it("downloads an authenticated Excel template and surfaces errors", async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const createObjectURL = vi.fn(() => "blob:financial-template");
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    let authorized = false;
    let fail = true;
    server.use(http.get(`${API}/api/vault/financial/template`, ({ request }) => {
      authorized = request.headers.get("Authorization") === "Bearer test-token";
      if (fail) return apiError(500, "error", "Template unavailable");
      return new HttpResponse("PK workbook", { headers: {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      } });
    }));
    renderWithApp(<UploadsView />);
    await userEvent.click(await screen.findByRole("button", { name: "Download sample Excel" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Template unavailable");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Download sample Excel" }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(createObjectURL).toHaveBeenCalled();
    expect(authorized).toBe(true);
  });

  it("restricts the person dropdown to the current non-admin", async () => {
    server.use(http.get(`${API}/api/me`, () => HttpResponse.json({
      ...me, member: { ...me.member, role: "member" },
    })));
    renderWithApp(<UploadsView />);
    const person = await screen.findByLabelText("Person row 1");
    expect(within(person).getAllByRole("option")).toHaveLength(1);
    expect(person).toHaveValue("Ann");
  });
});
