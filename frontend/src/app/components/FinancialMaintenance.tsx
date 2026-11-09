"use client";

import { useState, type FormEvent } from "react";
import { useApi } from "@/lib/auth";
import { errorMessage, useAsync } from "@/lib/hooks";
import type { FinanceRecord, FinancialEntry, Member } from "@/lib/types";

const control = "mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm";
const blank = {
  person_name: "", category: "", subcategory: "", currency: "", transaction_from: "",
  transaction_to: "", due_from: "", due_to: "", is_paid: "",
};
type Filters = typeof blank;

export function FinancialMaintenance({ members, version }: { members: Member[]; version: number }) {
  const api = useApi();
  const options = useAsync(() => api.financeOptions(), [api, version]);
  const [filters, setFilters] = useState<Filters>(blank);
  const [offset, setOffset] = useState(0);
  const [locked, setLocked] = useState(false);
  function change(key: keyof Filters, value: string) {
    setFilters((current) => ({ ...current, [key]: value }));
    setOffset(0);
  }
  const applied = Object.values(filters).some(Boolean);
  return <section aria-label="Financial maintenance" className="enterprise-panel space-y-4">
    <div><h2 className="section-title">Maintenance</h2>
      <p className="mt-1 text-sm text-slate-500">Apply at least one filter to review, update or permanently delete earlier financial entries.</p></div>
    {options.error ? <p role="alert">{errorMessage(options.error)} <button type="button" onClick={options.reload} className="underline">Retry options</button></p> : null}
    <fieldset disabled={locked} className="grid items-start gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <label className="text-sm">Person<select aria-label="Person" className={control} value={filters.person_name}
        onChange={(event) => change("person_name", event.target.value)}>
        <option value="">All people</option>{members.map((member) => <option key={member.id}>{member.display_name}</option>)}</select></label>
      <label className="text-sm">Income/Expense<select aria-label="Income/Expense" className={control} value={filters.category}
        onChange={(event) => change("category", event.target.value)}>
        <option value="">All types</option><option value="income">Income</option><option value="expense">Expense</option></select></label>
      <label className="text-sm">Subcategory<input aria-label="Subcategory" className={control} value={filters.subcategory} list="maintenance-subcategories"
        maxLength={80} onChange={(event) => change("subcategory", event.target.value)} /></label>
      <datalist id="maintenance-subcategories">{[...new Set(Object.values(options.data?.subcategories ?? {}).flat())].map((value) => <option key={value}>{value}</option>)}</datalist>
      <label className="text-sm">Currency<select aria-label="Currency" className={control} value={filters.currency}
        onChange={(event) => change("currency", event.target.value)}>
        <option value="">All currencies</option>{options.data?.currencies.map((code) => <option key={code}>{code}</option>)}</select></label>
      <label className="text-sm">Paid status<select aria-label="Paid status" className={control} value={filters.is_paid}
        onChange={(event) => change("is_paid", event.target.value)}>
        <option value="">All statuses</option><option value="true">Paid</option><option value="false">Unpaid</option></select></label>
      {(["transaction_from", "transaction_to", "due_from", "due_to"] as const).map((key) => <label key={key} className="text-sm">
        {{ transaction_from: "Transaction date from", transaction_to: "Transaction date to", due_from: "Payment due date from", due_to: "Payment due date to" }[key]}
        <input type="date" aria-label={{ transaction_from: "Transaction date from", transaction_to: "Transaction date to", due_from: "Payment due date from", due_to: "Payment due date to" }[key]}
          className={control} value={filters[key]} onChange={(event) => change(key, event.target.value)} /></label>)}
    </fieldset>
    <div className="action-bar">
      <button type="button" disabled={locked} onClick={() => { setFilters(blank); setOffset(0); }} className="text-sm text-indigo-600 underline disabled:opacity-50">Clear filters</button>
    </div>
    {applied ? <FinancialResults key={`${JSON.stringify(filters)}:${offset}`} filters={filters}
      offset={offset} setOffset={setOffset} members={members} version={version} onLock={setLocked} onChanged={options.reload} /> :
      <p className="text-sm text-slate-500">Select a filter to show financial entries.</p>}
  </section>;
}

function FinancialResults({ filters, offset, setOffset, members, version, onLock, onChanged }: {
  filters: Filters; offset: number; setOffset: (offset: number) => void; members: Member[]; onChanged: () => void;
  version: number; onLock: (locked: boolean) => void;
}) {
  const api = useApi();
  const results = useAsync(() => api.financialRecords(filters, offset), [api, version]);
  const [editing, setEditing] = useState<FinanceRecord | null>(null);
  const [deleting, setDeleting] = useState<FinanceRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  async function remove() {
    if (!deleting || busy) return;
    setBusy(true); setError(null);
    try {
      await api.deleteDocument(deleting.id);
      setDeleting(null); onLock(false); setNotice("Financial entry deleted.");
      results.setData((current) => current && ({ ...current, entries: current.entries.filter((row) => row.id !== deleting.id) }));
      results.reload(); onChanged();
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }
  return <div className="space-y-3">
    <button type="button" onClick={results.reload} disabled={busy || editing !== null || deleting !== null || results.loading} className="text-sm text-indigo-600 underline">Refresh entries</button>
    {results.loading ? <p role="status">Loading entries...</p> : null}
    {results.error ? <p role="alert">{errorMessage(results.error)}</p> : null}
    {error ? <p role="alert" className="text-red-700">{error}</p> : null}
    {notice ? <p role="status" className="text-emerald-700">{notice}</p> : null}
    {editing ? <FinancialEditor key={editing.id} entry={editing} members={members}
      onCancel={() => { setEditing(null); onLock(false); }} onSaved={() => {
        setEditing(null); onLock(false); results.reload(); onChanged(); setNotice("Financial entry updated.");
      }} /> : null}
    {deleting ? <section aria-label="Confirm financial deletion" className="space-y-3 rounded-lg border border-red-200 bg-red-50 p-4">
      <p>Delete {deleting.person_name} · {deleting.subcategory} · {deleting.currency} {deleting.amount}? This cannot be undone.</p>
      <button type="button" disabled={busy} onClick={() => void remove()} className="mr-4 text-red-700 underline">Confirm delete</button>
      <button type="button" disabled={busy} onClick={() => { setDeleting(null); onLock(false); setError(null); }}>Cancel</button>
    </section> : null}
    {!results.loading && !results.error && results.data?.entries.length === 0 ? <p>No financial entries match the selected filters.</p> : null}
    <ul className="divide-y divide-slate-200">{results.data?.entries.map((entry) => <li key={entry.id} className="space-y-2 py-4 text-sm">
      <p className="font-semibold">{entry.person_name} · {entry.category} · {entry.subcategory} · {entry.currency} {entry.amount}</p>
      <p className="whitespace-pre-wrap break-words text-slate-600">{entry.details}</p>
      <p>Transaction: {entry.transaction_date ?? "Undated"} · Due: {entry.payment_due_date ?? "Not set"} · {entry.is_paid ? "Paid" : "Unpaid"}</p>
      <div className="flex gap-4">
        <button type="button" disabled={busy || editing !== null || deleting !== null || results.loading}
          aria-label={`Edit financial entry ${entry.id}`} onClick={() => { setEditing(entry); onLock(true); setError(null); setNotice(null); }} className="text-indigo-600 underline">Edit</button>
        <button type="button" disabled={busy || editing !== null || deleting !== null || results.loading}
          aria-label={`Delete financial entry ${entry.id}`} onClick={() => { setDeleting(entry); onLock(true); setError(null); setNotice(null); }} className="text-red-700 underline">Delete</button>
      </div>
    </li>)}</ul>
    {offset > 0 || results.data?.has_next ? <nav aria-label="Financial entry pages" className="flex items-center gap-4 text-sm">
      <button type="button" disabled={offset === 0 || busy || editing !== null || deleting !== null || results.loading} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</button>
      <span>Page {Math.floor(offset / 50) + 1}</span>
      <button type="button" disabled={!results.data?.has_next || busy || editing !== null || deleting !== null || results.loading} onClick={() => setOffset(offset + 50)}>Next</button>
    </nav> : null}
  </div>;
}

function FinancialEditor({ entry, members, onSaved, onCancel }: {
  entry: FinanceRecord; members: Member[]; onSaved: () => void; onCancel: () => void;
}) {
  const api = useApi();
  const [draft, setDraft] = useState<FinancialEntry>({
    person_name: entry.person_name, category: entry.category === "income" ? "income" : "expense",
    subcategory: entry.subcategory, amount: String(entry.amount), currency: entry.currency,
    details: entry.details, transaction_date: entry.transaction_date,
    payment_due_date: entry.payment_due_date, is_paid: entry.is_paid,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError(null);
    try { await api.updateFinancialEntry(entry.id, draft); onSaved(); }
    catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }
  return <form aria-label="Edit financial entry" onSubmit={(event) => void save(event)} className="space-y-4 rounded-lg border border-slate-300 p-4">
    <h3 className="font-semibold">Edit {entry.person_name} · {entry.subcategory}</h3>
    {error ? <p role="alert" className="text-red-700">{error}</p> : null}
    <fieldset disabled={busy} className="grid gap-3 sm:grid-cols-2">
      <label className="text-sm">Edit person<select aria-label="Edit person" className={control} value={draft.person_name} onChange={(event) => setDraft({ ...draft, person_name: event.target.value })}>
        {members.map((member) => <option key={member.id}>{member.display_name}</option>)}</select></label>
      <label className="text-sm">Edit category<select aria-label="Edit category" className={control} value={draft.category} onChange={(event) => setDraft({ ...draft, category: event.target.value === "income" ? "income" : "expense" })}>
        <option value="income">Income</option><option value="expense">Expense</option></select></label>
      <label className="text-sm">Edit subcategory<input aria-label="Edit subcategory" required maxLength={80} className={control} list="maintenance-subcategories" value={draft.subcategory}
        onChange={(event) => setDraft({ ...draft, subcategory: event.target.value })} /></label>
      <label className="text-sm">Edit amount<input aria-label="Edit amount" required type="number" min="0.01" max="999999999999.99" step="0.01" className={control} value={draft.amount}
        onChange={(event) => setDraft({ ...draft, amount: event.target.value })} /></label>
      <label className="text-sm">Edit currency<input aria-label="Edit currency" required maxLength={3} className={control} value={draft.currency}
        onChange={(event) => setDraft({ ...draft, currency: event.target.value.toUpperCase() })} /></label>
      <label className="text-sm">Edit details<textarea aria-label="Edit details" required maxLength={2000} className={control} value={draft.details}
        onChange={(event) => setDraft({ ...draft, details: event.target.value })} /></label>
      <label className="text-sm">Edit transaction date<input aria-label="Edit transaction date" type="date" className={control} value={draft.transaction_date ?? ""}
        onChange={(event) => setDraft({ ...draft, transaction_date: event.target.value || null })} /></label>
      <label className="text-sm">Edit payment due date<input aria-label="Edit payment due date" type="date" className={control} value={draft.payment_due_date ?? ""}
        onChange={(event) => setDraft({ ...draft, payment_due_date: event.target.value || null })} /></label>
      <label className="text-sm">Edit paid status<select aria-label="Edit paid status" className={control} value={draft.is_paid ? "true" : "false"}
        onChange={(event) => setDraft({ ...draft, is_paid: event.target.value === "true" })}><option value="true">Paid</option><option value="false">Unpaid</option></select></label>
    </fieldset>
    <div className="action-bar">
    <button type="submit" disabled={busy} className="rounded-md bg-indigo-600 px-4 py-2 text-sm text-white">{busy ? "Saving..." : "Save changes"}</button>
    <button type="button" disabled={busy} onClick={onCancel} className="text-sm underline">Cancel edit</button>
    </div>
  </form>;
}
