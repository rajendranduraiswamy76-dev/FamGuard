"use client";

import Link from "next/link";
import { useRef, useState, type FormEvent } from "react";

import { useApi } from "@/lib/auth";
import { errorMessage } from "@/lib/hooks";
import type { Category, Member, UploadItem } from "@/lib/types";

// Keep these limits aligned with FastAPI's vault upload route and parsers.
export const MAX_DOCUMENT_BYTES = 10 * 1024 * 1024;
export const MAX_DOCUMENT_FILES = 10;
export const DOCUMENT_ACCEPT = ".pdf,.docx,.xlsx,.jpg,.jpeg,.png,.webp,.heic,.heif";

export function validateDocumentFiles(files: File[]): string | null {
  if (!files.length) return "Choose at least one document.";
  if (files.length > MAX_DOCUMENT_FILES) return "Choose at most 10 documents per upload.";
  for (const file of files) {
    const extension = file.name.toLowerCase().match(/\.[^.]+$/)?.[0];
    if (!extension || !DOCUMENT_ACCEPT.split(",").includes(extension)) {
      return `${file.name}: Choose a PDF, DOCX, XLSX, JPEG, PNG, WebP, or HEIC image. Use CSV import for CSV records.`;
    }
    if (file.size === 0) return `${file.name}: The document is empty.`;
    if (file.size > MAX_DOCUMENT_BYTES) return `${file.name}: Documents must be 10 MB or smaller.`;
  }
  return null;
}

export function DocumentUpload({
  members,
  defaultMemberId,
  onUploaded,
  categories = [],
  categoriesLoading = false,
}: {
  members: Member[];
  defaultMemberId: string;
  onUploaded?: () => void;
  categories?: Category[];
  categoriesLoading?: boolean;
}) {
  const api = useApi();
  const inputRef = useRef<HTMLInputElement>(null);
  const busyRef = useRef(false);
  const [memberId, setMemberId] = useState(defaultMemberId);
  const [category, setCategory] = useState("");
  const [documentDetails, setDocumentDetails] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploads, setUploads] = useState<UploadItem[]>([]);

  function chooseFiles(selected: File[]) {
    if (busyRef.current) return;
    const problem = validateDocumentFiles(selected);
    setError(problem);
    if (!problem) {
      setFiles(selected);
      setUploads([]);
    }
    if (inputRef.current) inputRef.current.value = "";
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busyRef.current) return;
    const problem = validateDocumentFiles(files);
    if (problem || !members.some((member) => member.id === memberId)) {
      setError(problem ?? "Choose an available family member.");
      return;
    }
    busyRef.current = true;
    setUploading(true);
    setError(null);
    setUploads([]);
    try {
      const accepted = await api.upload(
        files, memberId, "upload", category || undefined, documentDetails,
      );
      if (accepted.length !== files.length) {
        throw new Error("The API returned an incomplete upload result. Check the vault before retrying.");
      }
      setUploads(accepted);
      setFiles([]);
      setDocumentDetails("");
      onUploaded?.();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      busyRef.current = false;
      setUploading(false);
    }
  }

  return (
    <section aria-label="Document upload" className="enterprise-panel">
      <h2 className="section-title">Upload documents</h2>
      <p id="document-upload-help" className="mt-2 text-sm text-slate-500">
        PDF, DOCX, XLSX, JPEG, PNG, WebP, and HEIC/HEIF images. Up to 10 files, 10 MB each.
        Choose a category or let AI detect it. PDFs are limited to 10 pages.
      </p>
      <form onSubmit={submit} className="mt-6 grid items-start gap-6 lg:grid-cols-2">
        <div className="min-w-0 space-y-2">
        <label htmlFor="upload-member" className="block text-sm font-medium text-slate-600">
          Document owner
        </label>
        <select
          id="upload-member"
          value={memberId}
          disabled={uploading}
          onChange={(event) => setMemberId(event.target.value)}
          className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
        >
          {members.map((member) => (
            <option key={member.id} value={member.id}>{member.display_name}</option>
          ))}
        </select>
        <label htmlFor="upload-category" className="block text-sm font-medium text-slate-600">
          Category
        </label>
        <select id="upload-category" value={category}
          disabled={uploading || categoriesLoading}
          onChange={(event) => setCategory(event.target.value)}
          className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm">
          <option value="">Auto-detect</option>
          {categories.filter((item) => !item.id.startsWith("pending-")).map((item) => (
            <option key={item.id} value={item.category_slug}>{item.category_display_name}</option>
          ))}
        </select>
        <label htmlFor="document-details" className="block text-sm font-medium text-slate-600">
          Document details
        </label>
        <textarea
          id="document-details"
          value={documentDetails}
          onChange={(event) => setDocumentDetails(event.target.value)}
          disabled={uploading}
          maxLength={2000}
          rows={4}
          aria-describedby="document-details-help"
          placeholder="Describe the document, purpose, reference numbers, or keywords you will search for."
          className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
        <p id="document-details-help" className="text-xs text-slate-500">
          Optional, up to 2,000 characters. Saved with each selected file and indexed with its
          owner, category, and extracted content to help search. These details apply to all files in this upload.
        </p>
        </div>
        <div className="min-w-0 space-y-4">
        <button
          type="button"
          disabled={uploading}
          aria-describedby="document-upload-help"
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => {
            event.preventDefault();
            if (!busyRef.current) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            chooseFiles(Array.from(event.dataTransfer.files));
          }}
          className={`w-full rounded-lg border-2 border-dashed px-4 py-10 text-center text-sm disabled:opacity-50 ${
            dragging ? "border-indigo-500 bg-indigo-50" : "border-slate-300 text-slate-600"
          }`}
        >
          {uploading ? "Uploading documents..." : "Drop documents here, or click to choose"}
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={DOCUMENT_ACCEPT}
          disabled={uploading}
          aria-label="Choose documents"
          className="sr-only"
          onChange={(event) => chooseFiles(Array.from(event.target.files ?? []))}
        />
        {files.length > 0 ? (
          <ul aria-label="Selected documents" className="space-y-1 text-sm">
            {files.map((file, index) => (
              <li key={`${index}-${file.name}`} className="flex items-center justify-between gap-2">
                <span className="break-all">{file.name}</span>
                <button
                  type="button"
                  disabled={uploading}
                  aria-label={`Remove ${file.name}`}
                  onClick={() => {
                    setFiles((current) => current.filter((_, i) => i !== index));
                    setError(null);
                  }}
                  className="text-slate-500 underline disabled:opacity-50"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        <div className="action-bar justify-end">
        <button
          type="submit"
          disabled={uploading || files.length === 0}
          className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {uploading ? "Uploading..." : "Upload documents"}
        </button>
        </div>
        </div>
      </form>
      {error ? <p role="alert" className="mt-3 text-sm text-red-600">{error}</p> : null}
      {uploads.length > 0 ? (
        <div role="status" className="mt-3 space-y-2 text-sm">
          <p className="font-medium text-emerald-700">
            {uploads.length} {uploads.length === 1 ? "document" : "documents"} accepted for processing.
          </p>
          <ul aria-label="Accepted documents">
            {uploads.map((upload) => <li key={upload.thread_id} className="break-all">{upload.filename}</li>)}
          </ul>
          <p>
            Processing continues in the background. Check the dashboard for totals or{" "}
            <Link href="/reviews" className="text-indigo-600 underline">Approval Reviews</Link> for documents needing approval.
          </p>
        </div>
      ) : null}
    </section>
  );
}
