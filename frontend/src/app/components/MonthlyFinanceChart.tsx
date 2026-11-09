"use client";

import { useState } from "react";
import type { FinanceDashboard } from "@/lib/types";

export function financeMoney(value: number | string | null, currency: string) {
  if (value === null) return "Insufficient history";
  return `${currency} ${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function MonthlyFinanceChart({ months }: { months: FinanceDashboard["months"] }) {
  const [chosenMonth, setChosenMonth] = useState("");
  const [chosenCurrency, setChosenCurrency] = useState("");
  const availableMonths = [...new Set(months.map((entry) => entry.month))].sort();
  const month = availableMonths.includes(chosenMonth) ? chosenMonth : availableMonths.at(-1) ?? "";
  const currencies = months.filter((entry) => entry.month === month).map((entry) => entry.currency);
  const currency = currencies.includes(chosenCurrency) ? chosenCurrency : currencies[0] ?? "";
  const entry = months.find((item) => item.month === month && item.currency === currency);
  if (!entry) return <p className="mt-5 text-sm text-slate-600">No financial entries for this month view.</p>;

  const income = entry.income === null ? null : Number(entry.income);
  const expense = entry.expense === null ? null : Number(entry.expense);
  const sufficient = income !== null && expense !== null;
  const nonnegative = sufficient && income >= 0 && expense >= 0;
  const hasActivity = nonnegative && (income > 0 || expense > 0);
  // Scale before adding so the chart ratio remains bounded even for large amounts.
  const scale = hasActivity ? Math.max(income, expense) : 1;
  const incomePercent = hasActivity ? (income / scale) / (income / scale + expense / scale) * 100 : 0;
  const expensePercent = hasActivity ? 100 - incomePercent : 0;
  const chartLabel = !sufficient ? "Insufficient history" : !nonnegative
    ? "Negative amounts cannot be represented as proportions" : !hasActivity
      ? "No income or expenses" : `Income ${incomePercent.toFixed(1)}%, expenses ${expensePercent.toFixed(1)}%`;

  return <div className="mt-5 space-y-5">
    <div className="flex flex-wrap items-end gap-4">
      <label className="min-w-0 text-sm font-medium text-slate-600">Chart month
        <select aria-label="Chart month" value={month} onChange={(event) => setChosenMonth(event.target.value)}
          className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2">
          {availableMonths.map((value) => <option key={value}>{value}</option>)}
        </select>
      </label>
      <label className="min-w-0 text-sm font-medium text-slate-600">Chart currency
        <select aria-label="Chart currency" value={currency} onChange={(event) => setChosenCurrency(event.target.value)}
          className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2">
          {currencies.map((value) => <option key={value}>{value}</option>)}
        </select>
      </label>
      <p className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600">
        {entry.view === "projected" ? `Estimate (${entry.sample_months} months)` : "Actual to date"}
      </p>
    </div>
    <div className="grid items-center gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <figure className="min-w-0 text-center">
        <div className="relative mx-auto aspect-square w-full max-w-[260px]">
          <svg role="img" aria-label={`${month} ${currency}: ${chartLabel}`}
            viewBox="0 0 120 120" className="h-full w-full">
            <circle cx="60" cy="60" r="46" fill="none" stroke={hasActivity ? "#0e7490" : "#dfe3e8"} strokeWidth="16" />
            {hasActivity ? <circle data-testid="income-arc" cx="60" cy="60" r="46" fill="none"
              stroke="#1d4ed8" strokeWidth="16" pathLength="100"
              strokeDasharray={`${incomePercent} ${100 - incomePercent}`} transform="rotate(-90 60 60)" /> : null}
          </svg>
          <div aria-hidden="true" className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-sm font-semibold text-slate-900">{currency}</span>
            <span className="mt-1 text-xs text-slate-600">{month}</span>
          </div>
        </div>
        <figcaption className="mt-3 text-sm text-slate-600">{chartLabel}</figcaption>
        {hasActivity ? <p className="mt-1 text-xs text-slate-500">Share of income + expenses, not a savings rate.</p> : null}
      </figure>
      <dl className="min-w-0 space-y-3">
        {[
          { label: "Income", value: entry.income, colour: "bg-indigo-600", percent: incomePercent },
          { label: "Expenses", value: entry.expense, colour: "bg-cyan-700", percent: expensePercent },
        ].map((item) => <div key={item.label} className="rounded-lg border border-slate-200 bg-white p-4">
          <dt className="flex items-center gap-2 text-sm text-slate-600">
            <span aria-hidden="true" className={`h-3 w-3 rounded-full ${item.colour}`} />{item.label}
            {hasActivity ? <span className="ml-auto tabular-nums">{item.percent.toFixed(1)}%</span> : null}
          </dt>
          <dd className="mt-2 break-words text-xl font-semibold tabular-nums text-slate-900">{financeMoney(item.value, currency)}</dd>
        </div>)}
        <div className="rounded-lg border border-slate-200 bg-tab p-4">
          <dt className="text-sm text-slate-600">Net balance</dt>
          <dd className="mt-2 break-words text-xl font-semibold tabular-nums text-slate-900">{financeMoney(entry.net, currency)}</dd>
        </div>
      </dl>
    </div>
  </div>;
}
