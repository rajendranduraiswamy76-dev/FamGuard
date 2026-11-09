import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { FinancialMaintenance } from "@/app/components/FinancialMaintenance";
import { API, apiError, me, server } from "./msw";
import { renderWithApp } from "./render";

const record = {
  id: "f-1", person_name: "Ann", category: "expense", subcategory: "Rent",
  amount: "100.25", currency: "SGD", details: "Original rent details",
  transaction_date: "2026-10-10", payment_due_date: "2026-10-20", is_paid: true,
};

describe("Financial maintenance", () => {
  it("starts empty, sends combined filters, and hides results after clearing", async () => {
    const calls: URLSearchParams[] = [];
    server.use(http.get(`${API}/api/vault/financial/entries`, ({ request }) => {
      calls.push(new URL(request.url).searchParams);
      return HttpResponse.json({ entries: [record], has_next: false });
    }));
    renderWithApp(<FinancialMaintenance members={me.members} version={0} />);
    expect(screen.getByText("Select a filter to show financial entries.")).toBeInTheDocument();
    expect(calls).toHaveLength(0);
    await userEvent.selectOptions(screen.getByLabelText("Person"), "Ann");
    expect(await screen.findByText("Original rent details")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Income/Expense"), "expense");
    await userEvent.type(screen.getByLabelText("Subcategory"), "Rent");
    await userEvent.selectOptions(screen.getByLabelText("Currency"), "SGD");
    await userEvent.selectOptions(screen.getByLabelText("Paid status"), "false");
    await userEvent.type(screen.getByLabelText("Transaction date from"), "2026-10-01");
    await userEvent.type(screen.getByLabelText("Transaction date to"), "2026-10-31");
    await userEvent.type(screen.getByLabelText("Payment due date from"), "2026-10-10");
    await userEvent.type(screen.getByLabelText("Payment due date to"), "2026-10-25");
    await waitFor(() => expect(Object.fromEntries(calls.at(-1)!)).toEqual({
      person_name: "Ann", category: "expense", subcategory: "Rent", currency: "SGD", is_paid: "false",
      transaction_from: "2026-10-01", transaction_to: "2026-10-31",
      due_from: "2026-10-10", due_to: "2026-10-25", offset: "0",
    }));
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.queryByText("Original rent details")).not.toBeInTheDocument();
  });

  it("edits all fields, preserves retry on errors and deletes only after confirmation", async () => {
    let saved = record;
    let deleted = false;
    let fail = true;
    let deleteCalls = 0;
    let body: unknown;
    server.use(
      http.get(`${API}/api/vault/financial/entries`, () => HttpResponse.json({
        entries: deleted ? [] : [saved], has_next: false,
      })),
      http.put(`${API}/api/vault/financial/entries/f-1`, async ({ request }) => {
        body = await request.json();
        if (fail) return apiError(503, "unavailable", "Index unavailable");
        saved = { ...saved, details: "Corrected details", amount: "99.99" };
        return new HttpResponse(null, { status: 204 });
      }),
      http.delete(`${API}/api/vault/documents/f-1`, () => {
        deleteCalls += 1; deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithApp(<FinancialMaintenance members={me.members} version={0} />);
    await userEvent.selectOptions(screen.getByLabelText("Person"), "Ann");
    await userEvent.click(await screen.findByRole("button", { name: "Edit financial entry f-1" }));
    const form = within(screen.getByRole("form", { name: "Edit financial entry" }));
    await userEvent.selectOptions(form.getByLabelText("Edit person"), "John");
    await userEvent.selectOptions(form.getByLabelText("Edit category"), "income");
    await userEvent.clear(form.getByLabelText("Edit subcategory"));
    await userEvent.type(form.getByLabelText("Edit subcategory"), "Salary");
    await userEvent.clear(form.getByLabelText("Edit amount"));
    await userEvent.type(form.getByLabelText("Edit amount"), "99.99");
    await userEvent.clear(form.getByLabelText("Edit currency"));
    await userEvent.type(form.getByLabelText("Edit currency"), "NOK");
    await userEvent.clear(form.getByLabelText("Edit details"));
    await userEvent.type(form.getByLabelText("Edit details"), "Corrected details");
    await userEvent.clear(form.getByLabelText("Edit transaction date"));
    await userEvent.type(form.getByLabelText("Edit transaction date"), "2026-10-15");
    await userEvent.clear(form.getByLabelText("Edit payment due date"));
    await userEvent.type(form.getByLabelText("Edit payment due date"), "2026-11-15");
    await userEvent.click(form.getByRole("button", { name: "Save changes" }));
    expect(await form.findByRole("alert")).toBeInTheDocument();
    expect(form.getByLabelText("Edit details")).toHaveValue("Corrected details");
    fail = false;
    await userEvent.click(form.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Financial entry updated.")).toBeInTheDocument();
    expect(body).toEqual({
      person_name: "John", category: "income", subcategory: "Salary", amount: "99.99",
      currency: "NOK", details: "Corrected details", transaction_date: "2026-10-15",
      payment_due_date: "2026-11-15", is_paid: true,
    });
    await userEvent.click(await screen.findByRole("button", { name: "Delete financial entry f-1" }));
    expect(screen.getByRole("region", { name: "Confirm financial deletion" })).toHaveTextContent("cannot be undone");
    expect(deleteCalls).toBe(0);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(deleteCalls).toBe(0);
    await userEvent.click(screen.getByRole("button", { name: "Delete financial entry f-1" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(await screen.findByText("Financial entry deleted.")).toBeInTheDocument();
    expect(deleteCalls).toBe(1);
    await waitFor(() => expect(screen.queryByText("Corrected details")).not.toBeInTheDocument());
  });

  it("pages results and resets paging when a filter changes", async () => {
    const offsets: string[] = [];
    server.use(http.get(`${API}/api/vault/financial/entries`, ({ request }) => {
      const offset = new URL(request.url).searchParams.get("offset")!;
      offsets.push(offset);
      return HttpResponse.json({
        entries: [{ ...record, details: `Page offset ${offset}` }], has_next: offset === "0",
      });
    }));
    renderWithApp(<FinancialMaintenance members={me.members} version={0} />);
    await userEvent.selectOptions(screen.getByLabelText("Person"), "Ann");
    await screen.findByText("Page offset 0");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Page offset 50")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Paid status"), "true");
    expect(await screen.findByText("Page offset 0")).toBeInTheDocument();
    expect(offsets.at(-1)).toBe("0");
  });
});
