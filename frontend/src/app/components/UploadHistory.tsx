"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { useApi } from "@/lib/auth";
import { errorMessage, useAsync } from "@/lib/hooks";
import type { Category, DocumentStatus, Member, UploadStatus } from "@/lib/types";

const PAGE_SIZE = 50;

interface HistoryProps {
  members: Member[];
  categories?: Category[];
  categoriesLoading?: boolean;
  uploadVersion?: number;
  pollMs?: number;
}

interface Filters {
  memberId: string;
  category: string;
  offset: number;
}

const STATUS_LABELS: Record<DocumentStatus, string> = {
  processing: "Processing - not searchable yet",
  review_required: "Needs approval - not searchable yet",
  completed: "Indexed - ready to search",
  failed: "Processing failed",
  rejected: "Rejected",
};

export function UploadHistory(props: HistoryProps) {
  const [filters, setFilters] = useState<Filters>({ memberId: "", category: "", offset: 0 });
  return <UploadResults key={`${filters.memberId}:${filters.category}:${filters.offset}`}
    {...props} filters={filters} onFiltersChange={setFilters} />;
}

function UploadResults({
  members, categories = [], categoriesLoading = false, uploadVersion = 0, pollMs = 5_000,
  filters, onFiltersChange,
}: HistoryProps & {
  filters: Filters;
  onFiltersChange: (filters: Filters) => void;
}) {
  const api = useApi();
  const filtered = Boolean(filters.memberId || filters.category);
  const history = useAsync(() => filtered ? api.recentUploads({
    familyMemberId: filters.memberId, category: filters.category,
    limit: PAGE_SIZE + 1, offset: filters.offset,
  }) : Promise.resolve([]), [api, uploadVersion, filters.memberId, filters.category, filters.offset]);
  const documents = history.data?.slice(0, PAGE_SIZE);
  const hasNext = (history.data?.length ?? 0) > PAGE_SIZE;
  const [fileError, setFileError] = useState<string | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<UploadStatus | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const deleteBusyRef = useRef(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState<UploadStatus | null>(null);
  const [reviewBusy, setReviewBusy] = useState(false);
  const reviewBusyRef = useRef(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const pending = history.data?.some((upload) =>
    upload.status === "processing" || upload.status === "review_required");
  const reload = history.reload;

  useEffect(() => {
    if (!pending || history.error || deleting || reviewing) return;
    const timer = setInterval(reload, pollMs);
    return () => clearInterval(timer);
  }, [pending, history.error, reload, pollMs, deleting, reviewing]);

  async function confirmReview() {
    if (!reviewing || reviewBusyRef.current) return;
    reviewBusyRef.current = true;
    setReviewBusy(true);
    setReviewError(null);
    try {
      const updated = await api.reviewExtraction(reviewing.document_id);
      history.setData((current) => current?.map((item) =>
        item.document_id === updated.document_id ? updated : item));
      setNotice("Extraction review opened. Go to Reviews to verify and correct the document.");
      setReviewing(null);
      reload();
    } catch (err) {
      setReviewError(errorMessage(err));
    } finally {
      reviewBusyRef.current = false;
      setReviewBusy(false);
    }
  }

  async function confirmDelete() {
    if (!deleting || deleteBusyRef.current) return;
    const document = deleting;
    deleteBusyRef.current = true;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await api.deleteDocument(document.document_id);
      history.setData((current) => current?.filter((item) => item.document_id !== document.document_id));
      setNotice(`Deleted ${document.filename ?? document.document_name}.`);
      setDeleting(null);
      reload();
    } catch (err) {
      setDeleteError(errorMessage(err));
    } finally {
      deleteBusyRef.current = false;
      setDeleteBusy(false);
    }
  }

  async function openOriginal(documentId: string) {
    setFileError(null);
    setOpening(documentId);
    try {
      const { url } = await api.documentUrl(documentId);
      window.location.assign(url);
    } catch (err) {
      setFileError(errorMessage(err));
    } finally {
      setOpening(null);
    }
  }

  return (
    <section aria-label="Upload history" className="enterprise-panel">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="section-title">Uploaded documents</h3>
        <button type="button" onClick={reload} disabled={!filtered || history.loading || deleting !== null || reviewing !== null}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-50">
          Refresh uploads
        </button>
      </div>
      <p className="mt-2 text-sm text-slate-500">
        Filter by owner and category to find uploads, including chat attachments.
        Documents become searchable after indexing. Pending uploads refresh automatically.
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="maintenance-owner" className="block text-sm font-medium text-slate-600">
            Filter by document owner
          </label>
          <select id="maintenance-owner" value={filters.memberId} disabled={deleting !== null || reviewing !== null}
            onChange={(event) => onFiltersChange({ ...filters, memberId: event.target.value, offset: 0 })}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm">
            <option value="">All available owners</option>
            {members.map((member) => <option key={member.id} value={member.id}>{member.display_name}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="maintenance-category" className="block text-sm font-medium text-slate-600">
            Filter by category
          </label>
          <select id="maintenance-category" value={filters.category}
            disabled={deleting !== null || reviewing !== null || categoriesLoading}
            onChange={(event) => onFiltersChange({ ...filters, category: event.target.value, offset: 0 })}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm">
            <option value="">All categories</option>
            {categories.filter((category) => !category.id.startsWith("pending-")).map((category) => (
              <option key={category.id} value={category.category_slug}>{category.category_display_name}</option>
            ))}
          </select>
        </div>
      </div>
      {history.error ? (
        <p role="alert" className="mt-3 text-sm text-red-600">{errorMessage(history.error)}</p>
      ) : null}
      {fileError ? <p role="alert" className="mt-3 text-sm text-red-600">{fileError}</p> : null}
      {notice ? <p role="status" className="mt-3 text-sm text-emerald-700">{notice}</p> : null}
      {notice?.startsWith("Extraction review opened") ? (
        <Link href="/reviews" className="mt-2 inline-block text-sm text-indigo-600 underline">Open Reviews</Link>
      ) : null}
      {reviewing ? (
        <section aria-label="Confirm extraction review" className="mt-3 space-y-3 rounded-lg border border-amber-200 bg-amber-50 p-4">
          <h4 className="font-semibold">Review extraction for {reviewing.filename ?? reviewing.document_name}?</h4>
          <p className="text-sm">
            Keeps the original file and opens its saved extraction in Reviews for correction.
            Removes existing search entries until you approve the corrected text and dates.
          </p>
          {reviewError ? <p role="alert" className="text-sm text-red-700">{reviewError}</p> : null}
          <div className="flex gap-3">
            <button type="button" disabled={reviewBusy} onClick={() => void confirmReview()}
              className="rounded-md bg-red-700 px-3 py-2 text-sm text-white disabled:opacity-50">
              {reviewBusy ? "Opening review..." : "Confirm review"}
            </button>
            <button type="button" disabled={reviewBusy} onClick={() => { setReviewing(null); setReviewError(null); }}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm disabled:opacity-50">
              Cancel
            </button>
          </div>
        </section>
      ) : null}
      {deleting ? (
        <section aria-label="Confirm document deletion" className="mt-3 space-y-3 rounded-lg border border-red-200 bg-red-50 p-4">
          <h4 className="font-semibold text-red-800">Delete {deleting.filename ?? deleting.document_name}?</h4>
          <p className="text-sm text-red-700">
            This permanently removes the document, its stored original, and its search index entries. This cannot be undone.
          </p>
          {deleteError ? <p role="alert" className="text-sm text-red-700">{deleteError}</p> : null}
          <div className="flex gap-3">
            <button type="button" disabled={deleteBusy} onClick={() => void confirmDelete()}
              className="rounded-md bg-red-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-50">
              {deleteBusy ? "Deleting..." : "Confirm delete"}
            </button>
            <button type="button" disabled={deleteBusy}
              onClick={() => { setDeleting(null); setDeleteError(null); }}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm disabled:opacity-50">
              Cancel
            </button>
          </div>
        </section>
      ) : null}
      {!history.data && history.loading ? <p role="status" className="mt-3 text-sm">Loading uploads...</p> : null}
      {!filtered ? <p className="mt-3 text-sm text-slate-500">Select an owner or category filter to show uploaded documents.</p> : null}
      {filtered && history.data?.length === 0 && !history.error ? (
        <p className="mt-3 text-sm text-slate-500">
          {filters.memberId || filters.category ? "No uploaded documents match the selected filters." : "No uploaded documents yet."}
        </p>
      ) : null}
      <ul aria-label="Uploaded documents" aria-live="polite" className="mt-3 divide-y divide-slate-100">
        {documents?.map((upload) => (
          <li key={upload.thread_id} className="space-y-2 py-4 text-sm">
            <p className="break-all font-medium">{upload.document_name}</p>
            <p className="break-all text-xs text-slate-500">
              {upload.filename} · {members.find((member) => member.id === upload.family_member_id)?.display_name ?? "Family member"} · {upload.category}
            </p>
            {upload.document_details ? (
              <p className="whitespace-pre-wrap break-words text-sm text-slate-600">
                <span className="font-medium">Document details: </span>{upload.document_details}
              </p>
            ) : null}
            <p className={upload.status === "completed" ? "text-emerald-700" :
              upload.status === "failed" || upload.status === "rejected" ? "text-red-600" : "text-amber-700"}>
              {STATUS_LABELS[upload.status]}
            </p>
            {upload.error ? <p className="text-red-600">{upload.error}</p> : null}
            {upload.status === "review_required" ? (
              <Link href="/reviews" className="inline-block text-indigo-600 underline">Review document</Link>
            ) : upload.status === "completed" ? (
              <Link href="/" className="inline-block text-indigo-600 underline">Search vault</Link>
            ) : null}
            {upload.status === "completed" ? (
              <button type="button"
                disabled={history.loading || deleting !== null || reviewing !== null}
                aria-label={`Review extraction ${upload.filename ?? upload.document_name}`}
                onClick={() => { setReviewing(upload); setReviewError(null); setNotice(null); }}
                className="ml-3 text-indigo-600 underline disabled:opacity-50">
                Review extraction
              </button>
            ) : null}
            {upload.status === "failed" ? (
              <p className="text-xs text-slate-500">Resolve the error above, then select the original file to upload again.</p>
            ) : null}
            {upload.status !== "rejected" ? (
              <button type="button" disabled={opening !== null}
                onClick={() => void openOriginal(upload.document_id)}
                aria-label={`Open original ${upload.filename ?? upload.document_name}`}
                className="ml-3 text-indigo-600 underline disabled:opacity-50">
                {opening === upload.document_id ? "Opening..." : "Open original"}
              </button>
            ) : null}
            <button type="button"
              disabled={history.loading || deleting !== null || reviewing !== null || upload.status === "processing" || upload.status === "review_required"}
              aria-label={`Delete ${upload.filename ?? upload.document_name}`}
              onClick={() => { setDeleting(upload); setDeleteError(null); setNotice(null); }}
              className="ml-3 text-red-700 underline disabled:cursor-not-allowed disabled:opacity-50">
              Delete
            </button>
            {upload.status === "processing" || upload.status === "review_required" ? (
              <p className="text-xs text-slate-500">
                Deletion is available after processing finishes. For a held document, approve or reject it in Reviews first.
              </p>
            ) : null}
          </li>
        ))}
      </ul>
      {filters.offset > 0 || hasNext ? (
        <nav aria-label="Upload document pages" className="mt-3 flex items-center gap-3 text-sm">
          <button type="button" disabled={filters.offset === 0 || history.loading || deleting !== null || reviewing !== null}
            onClick={() => onFiltersChange({ ...filters, offset: Math.max(0, filters.offset - PAGE_SIZE) })}
            className="rounded-md border border-slate-300 px-3 py-2 disabled:opacity-50">
            Previous
          </button>
          <span>Page {Math.floor(filters.offset / PAGE_SIZE) + 1}</span>
          <button type="button" disabled={!hasNext || history.loading || deleting !== null || reviewing !== null}
            onClick={() => onFiltersChange({ ...filters, offset: filters.offset + PAGE_SIZE })}
            className="rounded-md border border-slate-300 px-3 py-2 disabled:opacity-50">
            Next
          </button>
        </nav>
      ) : null}
    </section>
  );
}
