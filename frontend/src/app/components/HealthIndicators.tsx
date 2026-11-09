"use client";

import { useEffect, useState } from "react";

import { useApi } from "@/lib/auth";
import type { Readiness } from "@/lib/types";

export const HEALTH_POLL_MS = 30_000;
const SERVICES = ["postgres", "redis", "pinecone"] as const;
const LABELS: Record<(typeof SERVICES)[number], string> = {
  postgres: "Database",
  redis: "Queue",
  pinecone: "Vector index",
};

type Indicator = "ok" | "error" | "unknown";

export function HealthIndicators({ pollMs = HEALTH_POLL_MS }: { pollMs?: number }) {
  const api = useApi();
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [unreachable, setUnreachable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const result = await api.readiness();
        if (!cancelled) {
          setReadiness(result);
          setUnreachable(false);
        }
      } catch {
        if (!cancelled) setUnreachable(true);
      }
    };
    void poll();
    const timer = setInterval(() => void poll(), pollMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [api, pollMs]);

  const state = (service: (typeof SERVICES)[number]): Indicator => {
    if (unreachable) return "error";
    const value = readiness?.checks[service];
    if (!readiness) return "unknown";
    return value === "ok" ? "ok" : "error";
  };

  return (
    <section aria-label="Service health" className="enterprise-panel">
      <h2 className="section-title">Service health</h2>
      {unreachable ? (
        <p className="mt-1 text-xs text-red-600">API unreachable — it may be waking up.</p>
      ) : null}
      <ul className="mt-4 divide-y divide-slate-200">
        {SERVICES.map((service) => {
          const s = state(service);
          return (
            <li key={service} className="flex flex-wrap items-center justify-between gap-3 py-4 text-sm">
              <span>{LABELS[service]}</span>
              <span className="flex items-center gap-2 rounded-full bg-white px-3 py-1" data-testid={`health-${service}`} data-state={s}>
                <span
                  aria-hidden
                  className={`h-2.5 w-2.5 rounded-full ${
                    s === "ok" ? "bg-emerald-500" : s === "error" ? "bg-red-500" : "bg-slate-300"
                  }`}
                />
                <span className="text-xs text-slate-500">
                  {s === "ok" ? "Operational" : s === "error" ? "Down" : "Checking…"}
                </span>
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
