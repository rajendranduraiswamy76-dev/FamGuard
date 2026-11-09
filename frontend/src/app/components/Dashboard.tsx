"use client";

import { useApi } from "@/lib/auth";
import { errorMessage, useAsync } from "@/lib/hooks";
import { useVault } from "@/lib/vault-context";

import { ChatAgent } from "./ChatAgent";
import { MetricCard, MetricCardSkeleton } from "./MetricCard";
import { URGENT_DAYS, UpcomingExpirations } from "./UpcomingExpirations";

function ErrorPanel({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
      <p>{message}</p>
      <button type="button" onClick={onRetry} className="mt-2 font-medium underline">
        Retry
      </button>
    </div>
  );
}

export function Dashboard() {
  const api = useApi();
  const vault = useVault();
  const metrics = useAsync(() => api.metrics(), [api]);

  const m = metrics.data;
  const urgent = m?.upcoming_expirations.filter((u) => u.days_remaining <= URGENT_DAYS).length ?? 0;

  return (
    <div className="space-y-6">
      <div className="page-heading">
        <div>
        <h1>Family vault</h1>
        <p>
          Documents, expiry alerts and AI search for {vault.data?.family.name ?? "your family"}.
        </p>
        </div>
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-5">
        <div className="min-w-0 space-y-6 lg:col-span-2">
          {metrics.error && !m ? (
            <ErrorPanel message={errorMessage(metrics.error)} onRetry={metrics.reload} />
          ) : (
            <div className="grid grid-cols-2 gap-4" aria-busy={metrics.loading}>
              {!m ? (
                Array.from({ length: 2 }, (_, i) => <MetricCardSkeleton key={i} />)
              ) : (
                <>
                  <MetricCard
                    label="Expiring ≤ 30 days"
                    value={m.upcoming_expirations.length}
                    hint={urgent ? `${urgent} within ${URGENT_DAYS} days` : undefined}
                    tone={urgent ? "danger" : m.upcoming_expirations.length ? "warning" : "default"}
                  />
                  <MetricCard
                    label="Pending reviews"
                    value={m.open_reviews}
                    hint={m.failed ? `${m.failed} failed uploads` : undefined}
                    tone={m.open_reviews ? "warning" : "default"}
                  />
                </>
              )}
            </div>
          )}
          <UpcomingExpirations items={m?.upcoming_expirations ?? []} />
          <UpcomingExpirations yearly items={(m?.year_expirations ?? []).filter(
            (item) => item.days_remaining >= 30 && item.days_remaining < 365,
          )} />
        </div>
        <div className="min-w-0 lg:col-span-3">
          <ChatAgent />
        </div>
      </div>
    </div>
  );
}
