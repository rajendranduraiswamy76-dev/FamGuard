"use client";

import { useRef, useState, type FormEvent } from "react";
import Link from "next/link";

import { useApi } from "@/lib/auth";
import { errorMessage, useAsync } from "@/lib/hooks";
import type { BulkImportResult, FinancialEntry, Member } from "@/lib/types";

const CURRENCIES = [
  "SGD", "USD", "INR", "EUR", "GBP", "AUD", "CAD", "MYR", "JPY", "CNY",
  "HKD", "CHF", "AED", "NZD", "THB", "IDR", "PHP", "KRW", "TWD", "SAR",
];
const MAX_EXCEL_BYTES = 5 * 1024 * 1024;
const inputClass = "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm";

interface EntryRow {
  id: number;
  person_name: string;
  category: "income" | "expense";
  subcategory: string;
  amount: string;
  currency: string;
  details: string;
  transaction_date: string;
  payment_due_date: string;
  is_paid: boolean;
}

export function FinancialUpload({
  members, defaultCurrency, onSaved,
}: {
  members: Member[];
  defaultCurrency: string;
  onSaved?: () => void;
}) {
  const api = useApi();
  const suggestions = useAsync(() => api.financeOptions(), [api]);
  const nextId = useRef(1);
  const newRow = (id: number): EntryRow => ({
    id, person_name: members[0]?.display_name ?? "", category: "income",
    subcategory: "", amount: "", currency: defaultCurrency,
    details: "", transaction_date: "", payment_due_date: "", is_paid: true,
  });
  const [rows, setRows] = useState<EntryRow[]>(() => [newRow(0)]);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<BulkImportResult | null>(null);
  const [excelFile, setExcelFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const names = [...new Set(members.map((member) => member.display_name))];

  function update<K extends keyof EntryRow>(id: number, key: K, value: EntryRow[K]) {
    setRows((current) => current.map((row) => row.id === id ? { ...row, [key]: value } : row));
    setReport(null);
  }

  async function upload(mode: "table" | "excel") {
    if (busyRef.current) return;
    setError(null);
    setReport(null);
    if (mode === "table") {
      const invalid = rows.findIndex((row) =>
        !row.person_name || !row.subcategory.trim() || !row.details.trim()
        || !/^\d+(\.\d{1,2})?$/.test(row.amount)
        || Number(row.amount) <= 0 || Number(row.amount) >= 1e12);
      if (invalid !== -1) {
        setError(`Row ${invalid + 1}: choose a person, add a subcategory and details, and enter a positive amount with at most two decimal places.`);
        return;
      }
      if (rows.some((row) => !row.is_paid && (row.category !== "expense" || !row.payment_due_date))) {
        setError("Unpaid bills must use Expense and have a payment due date.");
        return;
      }
      if (rows.some((row) => !(suggestions.data?.currencies ?? CURRENCIES).includes(row.currency))) {
        setError("Choose a valid currency code from the suggestions.");
        return;
      }
    } else {
      if (!excelFile) { setError("Choose a filled Excel workbook first."); return; }
      if (!excelFile.name.toLowerCase().endsWith(".xlsx")) {
        setError("Choose an Excel .xlsx file."); return;
      }
      if (!excelFile.size || excelFile.size > MAX_EXCEL_BYTES) {
        setError("Excel file must not be empty and must be 5 MB or smaller."); return;
      }
    }
    const selectedFile = excelFile;
    busyRef.current = true;
    setBusy(true);
    try {
      let result: BulkImportResult;
      if (mode === "table") {
        result = await api.financialEntries(rows.map((row): FinancialEntry => ({
          person_name: row.person_name, category: row.category, subcategory: row.subcategory.trim(),
          amount: row.amount, currency: row.currency, details: row.details.trim(),
          transaction_date: row.transaction_date || null,
          payment_due_date: row.payment_due_date || null, is_paid: row.is_paid,
        })));
      } else {
        if (!selectedFile) throw new Error("Choose a filled Excel workbook first.");
        result = await api.financialExcel(selectedFile);
      }
      setReport(result);
      if (result.accepted > 0) onSaved?.();
      suggestions.reload();
      if (mode === "table") {
        // Keep only rejected rows so retrying cannot duplicate successful entries.
        const rejected = new Set(result.errors.map((entry) => entry.row));
        if (!result.errors_truncated) {
          const remaining = rows.filter((_, index) => rejected.has(index + 1));
          setRows(remaining.length ? remaining : [newRow(nextId.current++)]);
        }
      } else if (result.accepted > 0) {
        setExcelFile(null);
        if (fileRef.current) fileRef.current.value = "";
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  async function downloadTemplate() {
    setDownloading(true);
    setError(null);
    try {
      const blob = await api.financialTemplate();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "famguard-income-expense.xlsx";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setDownloading(false);
    }
  }

  return (
    <section aria-label="Income/expense data upload" className="enterprise-panel">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="section-title">Income/expense data upload</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-600">
            Add financial entries below or upload a filled Excel template. Amounts keep their
            selected currency; no conversion is performed.
          </p>
        </div>
        <button type="button" disabled={downloading || busy} onClick={() => void downloadTemplate()}
          className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium hover:bg-slate-50 disabled:opacity-50">
          {downloading ? "Downloading..." : "Download sample Excel"}
        </button>
      </div>
      <p className="mt-3 text-sm text-slate-600">
        Select an existing person and Income/Expense. Subcategory and currency accept typed
        values from suggestions; new subcategories are saved with your entries.
        Add people in <Link href="/users" className="text-indigo-600 underline">Users &amp; Categories management</Link> (admin only).
        Add a transaction date for monthly totals; unpaid expenses need a due date for bill reminders.
      </p>
      {suggestions.error ? <p role="alert" className="mt-2 text-sm text-red-700">
        {errorMessage(suggestions.error)} <button type="button" onClick={suggestions.reload} className="underline">Retry suggestions</button>
      </p> : null}
      <datalist id="financial-currencies">
        {(suggestions.data?.currencies ?? CURRENCIES).map((currency) => <option key={currency} value={currency} />)}
      </datalist>
      {(["income", "expense"] as const).map((category) => (
        <datalist key={category} id={`financial-${category}-subcategories`}>
          {[...new Set([
            ...(suggestions.data?.subcategories[category] ?? (category === "income" ? ["Salary", "Bonus", "Interest"] : ["Groceries", "Rent", "Utilities"])),
            ...rows.filter((row) => row.category === category && row.subcategory).map((row) => row.subcategory),
          ])].map((value) => <option key={value} value={value} />)}
        </datalist>
      ))}
      <form onSubmit={(event: FormEvent) => { event.preventDefault(); void upload("table"); }} className="mt-5" noValidate>
        <div className="table-scroll">
          <table className="enterprise-table min-w-[1480px] table-fixed">
            <caption className="sr-only">Income and expense entry table</caption>
            <thead>
              <tr>
                {["Name of the person", "Category", "Subcategory", "Amount", "Currency", "Details", "Transaction date", "Payment due date", "Paid", "Actions"].map((label) => (
                  <th key={label} scope="col" className={`px-3 py-3 font-semibold ${label === "Details" ? "w-[17%]" : label === "Actions" || label === "Paid" ? "w-[6%]" : ""}`}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {rows.map((row, index) => (
                <tr key={row.id} className="align-top">
                  <td className="p-2"><select aria-label={`Person row ${index + 1}`} value={row.person_name} disabled={busy}
                    onChange={(event) => update(row.id, "person_name", event.target.value)} className={inputClass}>
                    {names.map((name) => <option key={name} value={name}>{name}</option>)}
                  </select></td>
                  <td className="p-2"><select aria-label={`Category row ${index + 1}`} value={row.category} disabled={busy}
                    onChange={(event) => update(row.id, "category", event.target.value === "income" ? "income" : "expense")} className={inputClass}>
                    <option value="income">Income</option><option value="expense">Expense</option>
                  </select></td>
                  <td className="p-2"><input aria-label={`Subcategory row ${index + 1}`} value={row.subcategory} maxLength={80} disabled={busy}
                    list={`financial-${row.category}-subcategories`}
                    placeholder="Salary, groceries..." onChange={(event) => update(row.id, "subcategory", event.target.value)} className={inputClass} /></td>
                  <td className="p-2"><input aria-label={`Amount row ${index + 1}`} type="number" min="0.01" max="999999999999.99"
                    step="0.01" value={row.amount} disabled={busy} placeholder="0.00"
                    onChange={(event) => update(row.id, "amount", event.target.value)} className={`${inputClass} text-right tabular-nums`} /></td>
                  <td className="p-2"><input aria-label={`Currency row ${index + 1}`} value={row.currency} disabled={busy}
                    list="financial-currencies" maxLength={3}
                    onChange={(event) => update(row.id, "currency", event.target.value.toUpperCase())} className={inputClass} /></td>
                  <td className="p-2"><textarea aria-label={`Details row ${index + 1}`} rows={2} value={row.details} maxLength={2000} disabled={busy}
                    placeholder="Transaction details" onChange={(event) => update(row.id, "details", event.target.value)} className={inputClass} /></td>
                  <td className="p-2"><input type="date" aria-label={`Transaction date row ${index + 1}`} value={row.transaction_date} disabled={busy}
                    onChange={(event) => update(row.id, "transaction_date", event.target.value)} className={inputClass} /></td>
                  <td className="p-2"><input type="date" aria-label={`Payment due date row ${index + 1}`} value={row.payment_due_date} disabled={busy}
                    onChange={(event) => update(row.id, "payment_due_date", event.target.value)} className={inputClass} /></td>
                  <td className="p-2"><select aria-label={`Paid row ${index + 1}`} value={row.is_paid ? "yes" : "no"} disabled={busy}
                    onChange={(event) => update(row.id, "is_paid", event.target.value === "yes")} className={inputClass}>
                    <option value="yes">Yes</option><option value="no">No</option>
                  </select></td>
                  <td className="p-2"><button type="button" aria-label={`Remove row ${index + 1}`} disabled={busy || rows.length === 1}
                    onClick={() => { setRows((current) => current.filter((entry) => entry.id !== row.id)); setReport(null); }}
                    className="px-1 py-2 text-sm text-red-700 underline disabled:opacity-40">Remove</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="action-bar mt-4 justify-between">
          <button type="button" disabled={busy || rows.length >= 100} onClick={() => setRows((current) => [...current, newRow(nextId.current++)])}
            className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium disabled:opacity-50">Add row</button>
          <button type="submit" disabled={busy || !names.length}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
            {busy ? "Saving..." : "Save table entries"}
          </button>
        </div>
      </form>
      <div className="mt-6 border-t border-slate-200 pt-5">
        <h3 className="text-sm font-semibold text-slate-800">Upload from Excel</h3>
        <p id="financial-excel-help" className="mt-1 text-sm text-slate-600">
          Download the sample, replace the example rows, and keep the Entries sheet and headers.
          Up to 5,000 rows, 5 MB. Use values, not formulas. Import each transaction only once.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <input ref={fileRef} type="file" accept=".xlsx" aria-label="Choose income/expense Excel"
            aria-describedby="financial-excel-help" disabled={busy}
            onChange={(event) => { setExcelFile(event.target.files?.[0] ?? null); setReport(null); setError(null); }}
            className="min-w-0 max-w-full text-sm file:mr-3 file:rounded-md file:border file:border-slate-300 file:bg-white file:px-3 file:py-2 file:text-sm" />
          <button type="button" disabled={busy || !excelFile} onClick={() => void upload("excel")}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">Upload Excel data</button>
        </div>
      </div>
      {error ? <p role="alert" className="mt-4 text-sm text-red-700">{error}</p> : null}
      {report ? (
        <div className="mt-4 space-y-2 text-sm" role="status">
          <p><strong>{report.accepted} saved</strong> · {report.rejected} rejected</p>
          {report.errors.length ? (
            <ul className="list-inside list-disc text-red-700">
              {report.errors.map((entry) => <li key={`${entry.row}-${entry.reason}`}>Row {entry.row}: {entry.reason}</li>)}
            </ul>
          ) : null}
          {report.errors_truncated ? <p>Only the first 200 errors are shown.</p> : null}
          {report.accepted > 0 ? <p className="text-slate-600">
            Saved entries are searchable in the vault. Do not upload accepted rows again.
            {report.rejected > 0 ? " For Excel, remove accepted rows before retrying rejected ones." : ""}
          </p> : null}
        </div>
      ) : null}
    </section>
  );
}
