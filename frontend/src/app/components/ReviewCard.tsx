"use client";

import { useState, type FormEvent } from "react";

import { useApi } from "@/lib/auth";
import { errorMessage } from "@/lib/hooks";
import type { Category, CorrectedMetadata, PendingReview } from "@/lib/types";
import { isIsoDate } from "@/lib/validation";

const REASON_LABELS: Record<string, string> = {
  expiration_unparsed: "Expiry date unreadable",
  expiration_missing: "Expiry date missing",
  low_confidence: "Low confidence",
  unknown_category: "Unknown category",
  user_requested: "Extraction correction requested",
};

export function validateCorrection(values: CorrectedMetadata): string | null {
  if (!values.document_name.trim()) return "Document name is required.";
  if (values.expiration_date && !isIsoDate(values.expiration_date)) {
    return "Expiry date must be a valid YYYY-MM-DD date.";
  }
  if (values.requires_alert && !values.expiration_date) {
    return "Documents with alerts need an expiry date.";
  }
  if (values.financial_value !== null && !Number.isFinite(values.financial_value)) {
    return "Value must be a number.";
  }
  return null;
}

function initialValues(review: PendingReview): CorrectedMetadata {
  const s = review.suggested_metadata;
  return {
    category: s.category ?? "miscellaneous",
    document_name: s.document_name ?? review.original_filename,
    expiration_date: s.expiration_date ?? null,
    requires_alert: s.requires_alert ?? false,
    extracted_text: s.extracted_text ?? review.raw_text_preview,
    financial_value: s.financial_value ?? null,
  };
}

export function ReviewCard({
  review,
  categories,
  memberName,
  onResolved,
}: {
  review: PendingReview;
  categories: Category[];
  memberName: string;
  onResolved: (threadId: string, decision: "approve" | "reject") => void;
}) {
  const api = useApi();
  const [values, setValues] = useState<CorrectedMetadata>(() => initialValues(review));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const confidence = Math.round(review.confidence_score * 100);
  const idPrefix = `review-${review.id}`;

  const update = <K extends keyof CorrectedMetadata>(key: K, value: CorrectedMetadata[K]) =>
    setValues((v) => ({ ...v, [key]: value }));

  async function resolve(decision: "approve" | "reject", event?: FormEvent) {
    event?.preventDefault();
    if (decision === "approve") {
      const problem = validateCorrection(values);
      if (problem) {
        setError(problem);
        return;
      }
    }
    setError(null);
    setBusy(decision);
    try {
      await api.resolveReview(review.thread_id, decision, decision === "approve" ? values : undefined);
      onResolved(review.thread_id, decision);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function openOriginal() {
    if (!review.document_id) return;
    try {
      const { url } = await api.documentUrl(review.document_id);
      window.open(url, "_blank", "noopener,noreferrer");
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <article
      aria-label={`Review ${review.original_filename}`}
      className="enterprise-panel"
    >
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <h2 className="section-title break-words">{review.original_filename}</h2>
          <p className="text-xs text-slate-500">
            {memberName} · uploaded {new Date(review.created_at).toLocaleDateString()}
          </p>
        </div>
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-medium ${
            confidence < 75 ? "bg-amber-50 text-amber-800" : "bg-emerald-50 text-emerald-700"
          }`}
        >
          {confidence}% confidence
        </span>
      </header>
      <ul className="mt-2 flex flex-wrap gap-1">
        {review.review_reasons.map((r) => (
          <li key={r} className="rounded bg-red-50 px-1.5 py-0.5 text-xs text-red-700">
            {REASON_LABELS[r] ?? r}
          </li>
        ))}
      </ul>
      {review.review_reasons.includes("user_requested") ? (
        <p className="mt-3 text-sm text-amber-800">
          Compare every date with Open original. Correct birth and issue dates in Extracted text
          and update Expiry date separately. Chat search is paused for this document until
          approval and reindexing. Confidence is the original model estimate, not verification.
        </p>
      ) : null}

      <form onSubmit={(e) => void resolve("approve", e)} className="mt-4 grid gap-3 sm:grid-cols-2" noValidate>
        <div>
          <label htmlFor={`${idPrefix}-name`} className="block text-xs font-medium text-slate-600">
            Document name
          </label>
          <input
            id={`${idPrefix}-name`}
            value={values.document_name}
            onChange={(e) => update("document_name", e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          />
        </div>
        <div>
          <label htmlFor={`${idPrefix}-category`} className="block text-xs font-medium text-slate-600">
            Category
          </label>
          <select
            id={`${idPrefix}-category`}
            value={values.category}
            onChange={(e) => update("category", e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            {categories.map((c) => (
              <option key={c.id} value={c.category_slug}>
                {c.category_display_name} ({c.category_slug})
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={`${idPrefix}-expiry`} className="block text-xs font-medium text-slate-600">
            Expiry date
          </label>
          <input
            id={`${idPrefix}-expiry`}
            type="date"
            value={values.expiration_date ?? ""}
            onChange={(e) => update("expiration_date", e.target.value || null)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          />
        </div>
        <div>
          <label htmlFor={`${idPrefix}-value`} className="block text-xs font-medium text-slate-600">
            Value
          </label>
          <input
            id={`${idPrefix}-value`}
            type="number"
            step="0.01"
            value={values.financial_value ?? ""}
            onChange={(e) =>
              update("financial_value", e.target.value === "" ? null : Number(e.target.value))
            }
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          />
        </div>
        <label className="flex items-center gap-2 text-sm sm:col-span-2">
          <input
            type="checkbox"
            checked={values.requires_alert}
            onChange={(e) => update("requires_alert", e.target.checked)}
          />
          Send expiry alerts for this document
        </label>
        <div className="sm:col-span-2">
          <label htmlFor={`${idPrefix}-text`} className="block text-xs font-medium text-slate-600">
            Extracted text
          </label>
          <textarea
            id={`${idPrefix}-text`}
            rows={3}
            value={values.extracted_text}
            onChange={(e) => update("extracted_text", e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 font-mono text-xs"
          />
        </div>

        {error ? (
          <p role="alert" className="text-sm text-red-600 sm:col-span-2">
            {error}
          </p>
        ) : null}

        <div className="action-bar sm:col-span-2">
          <button
            type="submit"
            disabled={busy !== null}
            className="rounded-md bg-emerald-700 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy === "approve" ? "Approving…" : "Approve"}
          </button>
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void resolve("reject")}
            className="rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium text-red-700 disabled:opacity-50"
          >
            {busy === "reject" ? "Rejecting…" : "Reject"}
          </button>
          {review.document_id ? (
            <button
              type="button"
              onClick={() => void openOriginal()}
              className="ml-auto text-sm text-indigo-600 underline"
            >
              View original
            </button>
          ) : null}
        </div>
      </form>
    </article>
  );
}
