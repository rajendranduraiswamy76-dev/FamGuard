"use client";

import { useApi } from "@/lib/auth";
import { errorMessage, useAsync } from "@/lib/hooks";
import { MetricCard, MetricCardSkeleton } from "./MetricCard";

export function VaultStatistics() {
  const api = useApi();
  const metrics = useAsync(() => api.metrics(), [api]);
  const data = metrics.data;
  if (metrics.error) return (
    <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-red-700">
      <p>{errorMessage(metrics.error)}</p>
      <button type="button" onClick={metrics.reload} className="mt-2 underline">Retry statistics</button>
    </div>
  );
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2" aria-busy={metrics.loading}>
        {!data ? <><MetricCardSkeleton /><MetricCardSkeleton /></> : <>
          <MetricCard label="Documents" value={data.documents_total} hint={`${data.processing} processing`} />
          <MetricCard label="Pinecone vectors" value={data.vectors_total} hint="namespace family-vault" />
        </>}
      </div>
      <section aria-label="Documents by category" className="enterprise-panel">
        <h2 className="section-title">By category</h2>
        {!data ? <p role="status" className="mt-3 text-sm">Loading statistics...</p> :
          data.by_category.length ? (
            <ul className="mt-4 divide-y divide-slate-200 text-sm">{data.by_category.map((category) => (
              <li key={category.category} className="flex flex-wrap justify-between gap-2 py-3">
                <span>{category.category}</span>
                <span className="text-slate-500">{category.documents} docs · {category.vectors} vectors</span>
              </li>
            ))}</ul>
          ) : <p className="mt-3 text-sm text-slate-500">No documents yet.</p>}
      </section>
    </div>
  );
}
