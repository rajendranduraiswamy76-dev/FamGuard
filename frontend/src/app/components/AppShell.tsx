"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { VaultProvider, useVault } from "@/lib/vault-context";

const NAV = [
  { href: "/", label: "Family Vault Query" },
  { href: "/uploads", label: "Uploads" },
  { href: "/financial-data", label: "Income & Expenses Data" },
  { href: "/finance", label: "Finance Dashboard" },
  { href: "/reviews", label: "Approval Reviews" },
  { href: "/users", label: "Users & Categories management", adminOnly: true },
  { href: "/health", label: "Service health" },
] as const;

function FamilyBadge() {
  const { data } = useVault();
  if (!data) return null;
  return (
    <span className="hidden max-w-sm truncate text-sm text-slate-500 sm:inline">
      {data.family.name} · {data.member.display_name} ({data.member.role})
    </span>
  );
}

export function AppShell({ userSlot, children }: { userSlot: ReactNode; children: ReactNode }) {
  const pathname = usePathname();
  return (
    <VaultProvider>
      <a href="#workspace" className="sr-only z-50 rounded-md bg-white px-4 py-2 text-indigo-700 focus:not-sr-only focus:fixed focus:left-4 focus:top-4">
        Skip to content
      </a>
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-panel shadow-sm">
        <div className="mx-auto w-full max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="flex min-h-20 min-w-0 items-center justify-between gap-4 py-4">
            <Link href="/" className="flex shrink-0 items-center gap-3 text-lg font-bold tracking-tight text-slate-900">
              <span aria-hidden="true" className="flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-600 text-sm font-bold text-white">FG</span>
              <span>FamGuard <span className="text-indigo-600">AI</span>
                <span className="hidden text-[11px] font-medium tracking-wide text-slate-500 sm:block">Family workspace</span>
              </span>
            </Link>
            <div className="flex min-w-0 items-center gap-3">
              <FamilyBadge />
              {userSlot}
            </div>
          </div>
          <nav aria-label="Main" className="flex flex-wrap gap-1.5 border-t border-slate-200 py-3">
            <Navigation pathname={pathname} />
          </nav>
        </div>
      </header>
      <main id="workspace" tabIndex={-1} className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 outline-none sm:px-6 sm:py-8 lg:px-8">{children}</main>
    </VaultProvider>
  );
}

function Navigation({ pathname }: { pathname: string }) {
  const { data } = useVault();
  return NAV.filter((item) => !("adminOnly" in item) || data?.member.role === "admin").map((item) => (
    <Link
      key={item.href}
      href={item.href}
      aria-current={pathname === item.href ? "page" : undefined}
      className={`inline-flex min-h-11 items-center rounded-lg border px-3 py-2 text-[13px] font-semibold ${
        pathname === item.href ? "border-blue-300 bg-tab-active text-indigo-700 shadow-sm" : "border-transparent bg-tab text-slate-700 hover:border-blue-200 hover:bg-tab-active"
      }`}
    >
      {item.label}
    </Link>
  ));
}
