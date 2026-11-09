import Link from "next/link";
import type { FinanceDashboard } from "@/lib/types";
import { financeMoney } from "./MonthlyFinanceChart";

function calendarDay(date: string) {
  const [year, month, day] = date.split("-").map(Number);
  return Date.UTC(year, month - 1, day);
}

export function PaymentSchedule({ data }: { data: FinanceDashboard }) {
  const estimates = data.expected_cashflows.filter((entry) => entry.category === "expense");
  const payments = [
    ...estimates.map((entry) => ({
      ...entry, key: `estimated-${entry.source_id}-${entry.payment_due_date}`, status: "Estimated",
      previous: entry.previous_due_date,
    })),
    ...data.upcoming_bills.map((entry) => ({
      ...entry, key: `recorded-${entry.id}`, status: "Recorded unpaid", previous: null,
    })),
  ].sort((a, b) => (a.payment_due_date ?? "").localeCompare(b.payment_due_date ?? "") || a.key.localeCompare(b.key));

  return <section aria-label="Upcoming bill payments" className="enterprise-panel">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <h2 className="section-title">Bill payments due in the next 30 days</h2>
      <span className="rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 text-xs font-semibold text-indigo-700">
        {payments.length} payments
      </span>
    </div>
    <p className="mt-2 text-sm text-slate-600">Recorded unpaid bills and estimates from last month&apos;s transaction history.
      Estimates use the Payment due day, or transaction day when absent; they do not create transactions or change actual totals.</p>
    <div className="mt-4 flex flex-wrap gap-2 text-xs text-slate-600">
      <span className="rounded-md border border-slate-200 bg-white px-3 py-2">{data.upcoming_bills.length} recorded unpaid</span>
      <span className="rounded-md border border-slate-200 bg-white px-3 py-2">{estimates.length} estimated</span>
    </div>
    {!payments.length ? <div className="mt-5 rounded-lg border border-dashed border-slate-300 p-5 text-sm text-slate-600">
      <p className="font-semibold">No upcoming payments</p>
      <p className="mt-2">No unpaid bills due in the next 30 days.</p>
      <p className="mt-1">No expected payments from last month&apos;s transaction history.</p>
    </div> : <ul aria-label="Payment schedule" className="mt-5 space-y-3">
      {payments.map((payment) => {
        const due = payment.payment_due_date;
        const days = due ? Math.round((calendarDay(due) - calendarDay(data.today)) / 86400000) : null;
        const urgent = days !== null && days <= 7;
        const timing = days === null ? "Date not set" : days < 0 ? "Overdue" : days === 0 ? "Due today" : `In ${days} ${days === 1 ? "day" : "days"}`;
        return <li key={payment.key} className="grid min-w-0 gap-3 rounded-lg border border-slate-200 bg-white p-4 sm:grid-cols-[minmax(0,1fr)_auto]">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <p className="break-words font-semibold text-slate-900">{payment.subcategory}</p>
              <span className={`rounded-full border px-2 py-1 text-xs font-medium ${payment.status === "Estimated"
                ? "border-indigo-200 bg-indigo-50 text-indigo-700" : "border-amber-200 bg-amber-50 text-amber-800"}`}>{payment.status}</span>
            </div>
            <p className="mt-2 break-words text-sm text-slate-600">{payment.person_name} · Expense</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-sm text-slate-600">{payment.details}</p>
            {payment.previous ? <p className="mt-2 text-xs text-slate-500">Previous due/day {payment.previous}</p> : null}
          </div>
          <div className="min-w-0 sm:text-right">
            <p className="break-words text-lg font-semibold tabular-nums text-slate-900">{financeMoney(payment.amount, payment.currency)}</p>
            <p className="mt-2 text-sm text-slate-600">{payment.status === "Estimated" ? "Expected" : "Due"} {due ?? "date not set"}</p>
            <span className={`mt-2 inline-block rounded-md px-2 py-1 text-xs font-semibold ${urgent
              ? "bg-amber-50 text-amber-800" : "bg-tab text-slate-700"}`}>{timing}</span>
          </div>
        </li>;
      })}
    </ul>}
    <div className="action-bar mt-5">
      <Link href="/financial-data" className="text-sm font-medium text-indigo-700 underline">Manage payment dates and Paid status</Link>
    </div>
  </section>;
}
