import type { ReactNode } from "react";

const TONES = {
  default: "border-slate-200",
  warning: "border-amber-300",
  danger: "border-red-300",
} as const;

export function MetricCard({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: keyof typeof TONES;
}) {
  return (
    <section
      aria-label={label}
      className={`enterprise-panel p-4 sm:p-4 ${TONES[tone]}`}
    >
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-2 text-3xl font-bold tabular-nums text-slate-900">{value}</p>
      {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
    </section>
  );
}

export function MetricCardSkeleton() {
  return (
    <div
      data-testid="metric-skeleton"
      className="h-[104px] animate-pulse rounded-xl border border-slate-200 bg-panel p-4"
    >
      <div className="h-3 w-24 rounded bg-slate-200" />
      <div className="mt-4 h-7 w-16 rounded bg-slate-200" />
    </div>
  );
}
