import { ClerkProvider, UserButton } from "@clerk/nextjs";
import type { Metadata } from "next";
import "./globals.css";

import { AppShell } from "@/app/components/AppShell";
import { ClerkTokenProvider, StaticTokenProvider } from "@/lib/auth";
import { isTestAuth } from "@/lib/env";

export const metadata: Metadata = {
  title: "FamGuard AI — Family Life Vault",
  description: "Secure family document vault with AI search and expiry alerts.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="flex min-h-full flex-col text-slate-900">
        {isTestAuth ? (
          <StaticTokenProvider>
            <AppShell userSlot={<span className="text-sm text-slate-500">Test user</span>}>
              {children}
            </AppShell>
          </StaticTokenProvider>
        ) : (
          <ClerkProvider>
            <ClerkTokenProvider>
              <AppShell userSlot={<UserButton />}>{children}</AppShell>
            </ClerkTokenProvider>
          </ClerkProvider>
        )}
      </body>
    </html>
  );
}
