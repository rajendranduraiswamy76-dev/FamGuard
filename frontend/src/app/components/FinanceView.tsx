"use client";

import Link from "next/link";
import { useState } from "react";
import { useApi } from "@/lib/auth";
import { errorMessage, useAsync } from "@/lib/hooks";
import type { FinanceDashboard } from "@/lib/types";
import { MonthlyFinanceChart, financeMoney as money } from "./MonthlyFinanceChart";
import { PaymentSchedule } from "./PaymentSchedule";

function ExpectedEntries({ entries, category }: {
  entries: FinanceDashboard["expected_cashflows"]; category: "income" | "expense";
}) {
  const matching = entries.filter((entry) => entry.category === category);
  return <section aria-label={category === "income" ? "Expected income" : "Expected payments"} className="min-w-0">
    {!matching.length ? <p className="mt-3 text-sm text-slate-500">
      No expected {category === "income" ? "income" : "payments"} from last month&apos;s transaction history.
    </p> : <ul className="mt-2 divide-y divide-slate-200">{matching.map((entry) => (
      <li key={`${entry.source_id}-${entry.payment_due_date}`} className="flex flex-wrap justify-between gap-3 py-4 text-sm">
        <div className="min-w-0 break-words">
          <p className="font-semibold">{entry.person_name} · {entry.subcategory}</p>
          <p className="mt-1 whitespace-pre-wrap text-slate-600">{entry.details}</p>
          <p className="mt-1 text-xs text-slate-500">Estimated · Previous due/day {entry.previous_due_date}</p>
        </div>
        <div className="text-right"><p className="font-semibold">{money(entry.amount, entry.currency)}</p>
          <p>Expected {entry.payment_due_date}</p></div>
      </li>
    ))}</ul>}
  </section>;
}

export function FinanceView() {
  const api = useApi();
  const dashboard = useAsync(() => api.financeDashboard(), [api]);
  const [view, setView] = useState("current");
  const [dates, setDates] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const data = dashboard.data;

  async function dateEntry(id: string) {
    if (!dates[id]) { setError("Choose a transaction date first."); return; }
    setSaving(id);
    setError(null);
    try {
      await api.dateFinancialEntry(id, dates[id]);
      dashboard.reload();
    } catch (err) { setError(errorMessage(err)); }
    finally { setSaving(null); }
  }

  return (
    <div className="space-y-6">
      <div className="page-heading">
        <div>
          <h1>Finance dashboard</h1>
          <p>Income, expenses, monthly estimates, and upcoming income and payments.</p>
        </div>
        <button type="button" onClick={dashboard.reload} disabled={dashboard.loading}
          className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm disabled:opacity-50">Refresh finance</button>
      </div>
      {dashboard.error ? <p role="alert" className="text-red-700">{errorMessage(dashboard.error)}</p> : null}
      {error ? <p role="alert" className="text-red-700">{error}</p> : null}
      {!data && dashboard.loading ? <p role="status">Loading finance...</p> : null}
      {data ? <>
        <section aria-label="Monthly finance" className="enterprise-panel">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="section-title">Monthly income and expenses</h2>
            <label className="flex flex-wrap items-center gap-2 text-sm">Month view
              <select aria-label="Month view" value={view} onChange={(event) => setView(event.target.value)}
                className="rounded-md border border-slate-300 px-3 py-2">
                <option value="past">Past 6 months</option><option value="current">Current month</option>
                <option value="projected">Projected next 3 months</option>
              </select>
            </label>
          </div>
          <p className="mt-3 text-sm text-slate-600">
            Actuals include paid entries with a transaction date through {data.today}.
            Estimates average up to six completed months, starting with the first recorded month
            per currency and including zero-activity months. Current partial month and unpaid
            bills are excluded. These are estimates, not scheduled income or guaranteed spending.
            Currencies are never combined or converted.
          </p>
          <MonthlyFinanceChart months={data.months.filter((month) => month.view === view)} />
          {data.months.length ? <details className="mt-5">
            <summary className="cursor-pointer text-sm font-medium text-indigo-700">View detailed monthly totals</summary>
            <div tabIndex={0} role="region" aria-label="Monthly totals table"
            className="table-scroll mt-5 focus-visible:outline-2 focus-visible:outline-indigo-600">
            <table className="enterprise-table min-w-[640px]">
              <thead><tr>{["Month", "Currency", "Income", "Expenses", "Net", "Basis"].map((name) =>
                <th key={name} scope="col" className="px-3 py-3 font-semibold">{name}</th>)}</tr></thead>
              <tbody>{data.months.filter((month) => month.view === view).map((month) => (
                <tr key={`${month.month}-${month.currency}`} className="border-b border-slate-100">
                  <td className="p-3">{month.month}</td><td className="p-3">{month.currency}</td>
                  <td className="p-3 tabular-nums">{money(month.income, month.currency)}</td>
                  <td className="p-3 tabular-nums">{money(month.expense, month.currency)}</td>
                  <td className="p-3 tabular-nums">{money(month.net, month.currency)}</td>
                  <td className="p-3">{month.view === "projected" ? `Estimate (${month.sample_months} months)` : "Actual to date"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div></details> : <p className="mt-4 text-sm">No financial entries yet. <Link href="/financial-data" className="text-indigo-600 underline">Add income or expenses</Link>.</p>}
        </section>
        <div className="grid items-start gap-6 xl:grid-cols-2">
        <section aria-label="Expected income and payments" className="enterprise-panel">
          <h2 className="section-title">Upcoming income in the next 30 days</h2>
          <p className="mt-2 text-sm text-slate-600">
            Automatically repeats last calendar month&apos;s transaction history, including paid entries.
            Uses the Payment due day when provided, otherwise the transaction day
            (or the last day for shorter months).
            These are estimates only: no transactions are created and actual totals are unchanged.
            A matching recorded entry replaces its estimate, including when already paid.
          </p>
          <ExpectedEntries entries={data.expected_cashflows} category="income" />
        </section>
        <PaymentSchedule data={data} />
        </div>
        {data.undated_count > 0 ? <section aria-label="Undated financial entries" className="enterprise-panel">
          <h2 className="section-title">Undated entries ({data.undated_count})</h2>
          <p className="mt-2 text-sm text-slate-600">Excluded from monthly totals and projections. Assign their actual transaction date below. Showing up to 50; refresh after saving to see more.</p>
          <ul className="mt-4 divide-y divide-slate-200">{data.undated.map((entry) => (
            <li key={entry.id} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm">
              <div><p className="font-semibold">{entry.person_name} · {entry.subcategory} · {money(entry.amount, entry.currency)}</p>
                <p className="break-words text-slate-600">{entry.details}</p></div>
              <div className="flex max-w-full flex-wrap items-center gap-2">
                <input aria-label={`Transaction date for ${entry.id}`} type="date" value={dates[entry.id] ?? ""}
                  onChange={(event) => setDates((current) => ({ ...current, [entry.id]: event.target.value }))}
                  className="rounded-md border border-slate-300 px-3 py-2" />
                <button type="button" disabled={saving !== null || dashboard.loading}
                  onClick={() => void dateEntry(entry.id)} aria-label={`Save date for ${entry.id}`}
                  className="rounded-md bg-indigo-600 px-3 py-2 text-white disabled:opacity-50">Save date</button>
              </div>
            </li>
          ))}</ul>
        </section> : null}
      </> : null}
    </div>
  );
}
