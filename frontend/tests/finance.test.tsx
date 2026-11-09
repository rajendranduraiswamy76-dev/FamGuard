import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { FinanceView } from "@/app/components/FinanceView";
import { MonthlyFinanceChart } from "@/app/components/MonthlyFinanceChart";
import type { FinanceDashboard } from "@/lib/types";
import { API, apiError, server } from "./msw";
import { renderWithApp } from "./render";

const record = {
  id: "undated-1", person_name: "Ann", category: "expense", subcategory: "Utilities",
  amount: "45.50", currency: "SGD", details: "Electricity bill",
  transaction_date: null, payment_due_date: "2026-11-20", is_paid: false,
};
const summary = {
  today: "2026-11-09",
  months: [
    { month: "2026-10", view: "past", currency: "SGD", income: "500", expense: "150", net: "350", sample_months: 0 },
    { month: "2026-11", view: "current", currency: "SGD", income: "300", expense: "100", net: "200", sample_months: 0 },
    { month: "2026-12", view: "projected", currency: "SGD", income: "500", expense: "150", net: "350", sample_months: 3 },
    { month: "2026-12", view: "projected", currency: "NOK", income: null, expense: null, net: null, sample_months: 0 },
  ] satisfies FinanceDashboard["months"],
  expected_cashflows: [
    { source_id: "old-income", person_name: "Ann", category: "income", subcategory: "Salary",
      amount: "3000.00", currency: "SGD", details: "Monthly salary",
      payment_due_date: "2026-11-25", previous_due_date: "2026-10-25" },
    { source_id: "old-rent", person_name: "Ann", category: "expense", subcategory: "Rent",
      amount: "900.00", currency: "NOK", details: "Monthly rent",
      payment_due_date: "2026-12-01", previous_due_date: "2026-10-01" },
  ],
  upcoming_bills: [record], undated: [record], undated_count: 1,
};

describe("Finance dashboard", () => {
  it("separates automatically expected income and payments from recorded bills and totals", async () => {
    server.use(http.get(`${API}/api/vault/financial/dashboard`, () => HttpResponse.json(summary)));
    renderWithApp(<FinanceView />);
    const income = within(await screen.findByRole("region", { name: "Expected income" }));
    expect(income.getByText("SGD 3,000.00")).toBeInTheDocument();
    expect(income.getByText("Expected 2026-11-25")).toBeInTheDocument();
    expect(income.getByText("Estimated · Previous due/day 2026-10-25")).toBeInTheDocument();
    const payments = within(screen.getByRole("list", { name: "Payment schedule" }));
    expect(payments.getByText("NOK 900.00")).toBeInTheDocument();
    expect(payments.getByText("Expected 2026-12-01")).toBeInTheDocument();
    expect(income.queryByText("Monthly rent")).not.toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Upcoming bill payments" })).getByText("Monthly rent")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "2026-11 SGD: Income 75.0%, expenses 25.0%" })).toBeInTheDocument();
    expect(payments.getByText("Estimated")).toBeInTheDocument();
    expect(payments.getByText("Recorded unpaid")).toBeInTheDocument();
    expect(payments.getAllByRole("listitem")[0]).toHaveTextContent("Electricity bill");
  });

  it("shows current, past and projected views without combining currencies", async () => {
    server.use(http.get(`${API}/api/vault/financial/dashboard`, () => HttpResponse.json(summary)));
    renderWithApp(<FinanceView />);
    const monthly = within(await screen.findByRole("region", { name: "Monthly finance" }));
    expect(await monthly.findByRole("img", { name: /2026-11 SGD/ })).toBeInTheDocument();
    expect(monthly.queryByText("2026-10")).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Month view"), "past");
    expect(screen.getByLabelText("Chart month")).toHaveValue("2026-10");
    await userEvent.selectOptions(screen.getByLabelText("Month view"), "projected");
    expect(monthly.getAllByText("Estimate (3 months)")).toHaveLength(2);
    await userEvent.click(monthly.getByText("View detailed monthly totals"));
    expect(within(monthly.getByRole("region", { name: "Monthly totals table" })).getAllByText("Insufficient history")).toHaveLength(3);
    await userEvent.selectOptions(screen.getByLabelText("Chart currency"), "NOK");
    expect(screen.getByRole("img", { name: "2026-12 NOK: Insufficient history" })).toBeInTheDocument();
    expect(screen.queryByTestId("income-arc")).not.toBeInTheDocument();
    const bills = within(screen.getByRole("region", { name: "Upcoming bill payments" }));
    expect(bills.getByText("Due 2026-11-20")).toBeInTheDocument();
    expect(bills.getByText("Electricity bill")).toBeInTheDocument();
  });

  it("allows dating legacy entries, refreshes data, and surfaces failure", async () => {
    let body: unknown;
    let dated = false;
    server.use(
      http.get(`${API}/api/vault/financial/dashboard`, () => HttpResponse.json({
        ...summary, undated: dated ? [] : summary.undated, undated_count: dated ? 0 : 1,
      })),
      http.put(`${API}/api/vault/financial/entries/undated-1/date`, async ({ request }) => {
        body = await request.json();
        dated = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithApp(<FinanceView />);
    await screen.findByRole("region", { name: "Undated financial entries" });
    await userEvent.click(screen.getByRole("button", { name: "Save date for undated-1" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a transaction date first");
    await userEvent.type(screen.getByLabelText("Transaction date for undated-1"), "2026-10-15");
    await userEvent.click(screen.getByRole("button", { name: "Save date for undated-1" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Undated financial entries" })).not.toBeInTheDocument());
    expect(body).toEqual({ transaction_date: "2026-10-15" });
  });

  it("shows an explicit API error and offers refresh", async () => {
    server.use(http.get(`${API}/api/vault/financial/dashboard`, () => apiError(500, "error", "Finance unavailable")));
    renderWithApp(<FinanceView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Finance unavailable");
    server.use(http.get(`${API}/api/vault/financial/dashboard`, () => HttpResponse.json({
      today: "2026-11-09", months: [], expected_cashflows: [], upcoming_bills: [], undated: [], undated_count: 0,
    })));
    await userEvent.click(screen.getByRole("button", { name: "Refresh finance" }));
    expect(await screen.findByText("No unpaid bills due in the next 30 days.")).toBeInTheDocument();
    expect(screen.getByText("No expected income from last month's transaction history.")).toBeInTheDocument();
  });

  it("changes the chart month without combining currencies and keeps net separate from the donut", async () => {
    renderWithApp(<MonthlyFinanceChart months={[
      summary.months[0],
      { ...summary.months[0], month: "2026-09", income: "100", expense: "300", net: "-200" },
      { ...summary.months[0], currency: "USD", income: "200", expense: "0", net: "200" },
    ]} />);
    expect(screen.getByLabelText("Chart month")).toHaveValue("2026-10");
    await userEvent.selectOptions(screen.getByLabelText("Chart currency"), "USD");
    expect(screen.getByRole("img", { name: "2026-10 USD: Income 100.0%, expenses 0.0%" })).toBeInTheDocument();
    expect(screen.getByTestId("income-arc")).toHaveAttribute("stroke-dasharray", "100 0");
    await userEvent.selectOptions(screen.getByLabelText("Chart month"), "2026-09");
    expect(screen.getByLabelText("Chart currency")).toHaveValue("SGD");
    expect(screen.getByRole("img", { name: "2026-09 SGD: Income 25.0%, expenses 75.0%" })).toBeInTheDocument();
    expect(screen.getByText("SGD -200.00")).toBeInTheDocument();
    expect(screen.getByTestId("income-arc")).toHaveAttribute("stroke-dasharray", "25 75");
  });

  it.each([
    ["0", "0", "No income or expenses"],
    ["0", "50", "Income 0.0%, expenses 100.0%"],
    [null, null, "Insufficient history"],
    ["-10", "20", "Negative amounts cannot be represented as proportions"],
  ])("handles income %s and expenses %s without a misleading chart", (income, expense, label) => {
    renderWithApp(<MonthlyFinanceChart months={[{ ...summary.months[0], income, expense }]} />);
    expect(screen.getByRole("img", { name: `2026-10 SGD: ${label}` })).toBeInTheDocument();
    if (label !== "Income 0.0%, expenses 100.0%") expect(screen.queryByTestId("income-arc")).not.toBeInTheDocument();
  });

  it("displays due today and seven-day urgency without marking later bills urgent", async () => {
    server.use(http.get(`${API}/api/vault/financial/dashboard`, () => HttpResponse.json({
      ...summary, expected_cashflows: [], upcoming_bills: [
        { ...record, id: "later", payment_due_date: "2026-11-17" },
        { ...record, id: "week", payment_due_date: "2026-11-16" },
        { ...record, id: "now", payment_due_date: "2026-11-09" },
      ],
    })));
    renderWithApp(<FinanceView />);
    const list = within(await screen.findByRole("list", { name: "Payment schedule" }));
    expect(list.getAllByRole("listitem")[0]).toHaveTextContent("Due today");
    expect(list.getByText("Due today")).toHaveClass("text-amber-800");
    expect(list.getByText("In 7 days")).toHaveClass("text-amber-800");
    expect(list.getByText("In 8 days")).toHaveClass("text-slate-700");
    expect(screen.getByText("3 payments")).toBeInTheDocument();
  });
});
