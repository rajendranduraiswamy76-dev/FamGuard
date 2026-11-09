"use client";

import { useRef, useState, type DragEvent } from "react";

import { useApi } from "@/lib/auth";
import { buildTemplateCsv, downloadTextFile } from "@/lib/csv";
import { errorMessage } from "@/lib/hooks";
import type { BulkImportResult, Member } from "@/lib/types";

export const MAX_CSV_BYTES = 5 * 1024 * 1024;

export function validateCsvFile(file: File): string | null {
  if (!file.name.toLowerCase().endsWith(".csv")) return "Please choose a .csv file.";
  if (file.size > MAX_CSV_BYTES) return "The CSV must be 5 MB or smaller.";
  if (file.size === 0) return "The CSV file is empty.";
  return null;
}

export function BulkUpload({ members }: { members: Member[] }) {
  const api = useApi();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BulkImportResult | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);

  async function handleFile(file: File | undefined) {
    if (!file) return;
    setResult(null);
    const problem = validateCsvFile(file);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setFileName(file.name);
    setUploading(true);
    try {
      setResult(await api.bulkUpload(file));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    void handleFile(event.dataTransfer.files[0]);
  }

  return (
    <section aria-label="Bulk upload" className="rounded-xl border border-slate-200 bg-panel p-4 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-700">Bulk CSV import</h2>
        <button
          type="button"
          onClick={() => downloadTextFile("famguard-template.csv", buildTemplateCsv(members))}
          className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
        >
          Download sample CSV
        </button>
      </div>

      <div
        role="button"
        tabIndex={0}
        data-dragging={dragging}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`mt-3 flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-6 text-center text-sm ${
          dragging ? "border-indigo-500 bg-indigo-50" : "border-slate-300 text-slate-600"
        }`}
      >
        {uploading ? `Importing ${fileName ?? "CSV"}…` : "Drop a CSV here, or click to choose (max 5 MB)"}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept=".csv,text/csv"
        aria-label="Choose CSV file"
        className="sr-only"
        data-testid="bulk-file-input"
        onChange={(e) => void handleFile(e.target.files?.[0])}
      />

      {error ? (
        <p role="alert" className="mt-3 text-sm text-red-600">
          {error}
        </p>
      ) : null}

      {result ? (
        <div className="mt-3 space-y-2" role="status">
          <p className="text-sm">
            <span className="font-semibold text-emerald-700">{result.accepted} imported</span>
            {" · "}
            <span className={result.rejected ? "font-semibold text-red-700" : "text-slate-500"}>
              {result.rejected} rejected
            </span>
          </p>
          {result.errors.length > 0 ? (
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="text-slate-500">
                  <th className="py-1 pr-2 font-medium">Row</th>
                  <th className="py-1 font-medium">Problem</th>
                </tr>
              </thead>
              <tbody>
                {result.errors.map((e) => (
                  <tr key={`${e.row}-${e.reason}`} className="border-t border-slate-100">
                    <td className="py-1 pr-2 tabular-nums">{e.row}</td>
                    <td className="py-1">{e.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {result.errors_truncated ? (
            <p className="text-xs text-slate-500">Only the first 200 errors are shown.</p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
