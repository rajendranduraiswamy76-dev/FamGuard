"use client";

import { useState } from "react";

import { useApi } from "@/lib/auth";
import { errorMessage, useAsync } from "@/lib/hooks";
import { useVault } from "@/lib/vault-context";

import { ReviewCard } from "./ReviewCard";

export function ReviewsView() {
  const api = useApi();
  const vault = useVault();
  const reviews = useAsync(() => api.pendingReviews(), [api]);
  const categories = useAsync(() => api.categories(), [api]);
  const [notice, setNotice] = useState<string | null>(null);

  const memberName = (id: string) =>
    vault.data?.members.find((m) => m.id === id)?.display_name ?? "Family member";

  return (
    <div className="space-y-6">
      <div className="page-heading">
        <div>
        <h1>Pending reviews</h1>
        <p>
          Documents the AI wasn&apos;t sure about. Confirm or correct the details before they enter the vault.
        </p>
        </div>
      </div>
      {notice ? (
        <p role="status" className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          {notice}
        </p>
      ) : null}
      {reviews.error ? (
        <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {errorMessage(reviews.error)}{" "}
          <button type="button" onClick={reviews.reload} className="font-medium underline">
            Retry
          </button>
        </div>
      ) : reviews.loading && !reviews.data ? (
        <p className="text-sm text-slate-500">Loading reviews…</p>
      ) : reviews.data && reviews.data.length === 0 ? (
        <p className="enterprise-panel py-12 text-center text-sm text-slate-500">
          All caught up — nothing needs review.
        </p>
      ) : (
        <div className="grid items-start gap-6 xl:grid-cols-2">
          {reviews.data?.map((review) => (
            <ReviewCard
              key={review.id}
              review={review}
              categories={categories.data ?? []}
              memberName={memberName(review.family_member_id)}
              onResolved={(threadId, decision) => {
                reviews.setData((list) => list?.filter((r) => r.thread_id !== threadId));
                setNotice(
                  decision === "approve"
                    ? `Approved ${review.original_filename}; it will appear in the vault shortly.`
                    : `Rejected ${review.original_filename}.`,
                );
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
