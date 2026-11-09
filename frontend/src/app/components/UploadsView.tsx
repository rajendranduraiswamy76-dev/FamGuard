"use client";

import { useState } from "react";

import { useApi } from "@/lib/auth";
import { errorMessage, useAsync } from "@/lib/hooks";
import { useVault } from "@/lib/vault-context";

import { DocumentUpload } from "./DocumentUpload";
import { UploadHistory } from "./UploadHistory";

export function UploadsView() {
  const api = useApi();
  const vault = useVault();
  const categories = useAsync(() => api.categories(), [api]);
  const [uploadVersion, setUploadVersion] = useState(0);

  if (vault.error) {
    return (
      <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-red-700">
        <p>{errorMessage(vault.error)}</p>
        <button type="button" onClick={vault.reload} className="mt-2 underline">Retry</button>
      </div>
    );
  }
  if (!vault.data) return <p role="status">Loading family...</p>;

  const { member, members } = vault.data;
  const isAdmin = member.role === "admin";

  return (
    <div className="space-y-6">
      <div className="page-heading">
        <div>
        <h1>Uploads</h1>
        <p>
          Upload family documents. Manage financial entries in Income &amp; Expenses Data.
        </p>
        </div>
      </div>
      <DocumentUpload
        members={isAdmin ? members : [member]}
        defaultMemberId={member.id}
        onUploaded={() => setUploadVersion((version) => version + 1)}
        categories={categories.data ?? []}
        categoriesLoading={categories.loading}
      />
      <section aria-label="Maintenance" className="space-y-3">
        <h2 className="text-lg font-semibold">Maintenance</h2>
        <p className="text-sm text-slate-500">Review uploaded documents, retrieve originals, or permanently delete documents you no longer need.</p>
        <UploadHistory members={isAdmin ? members : [member]}
          categories={categories.data ?? []} categoriesLoading={categories.loading}
          uploadVersion={uploadVersion} />
      </section>
        {categories.error ? (
          <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-red-700">
            <p>{errorMessage(categories.error)}</p>
            <button type="button" onClick={categories.reload} className="mt-2 underline">Retry categories</button>
          </div>
        ) : null}
    </div>
  );
}
