"use client";

import { useAuth } from "@clerk/nextjs";
import { createContext, useContext, useMemo, type ReactNode } from "react";

import { createApiClient, type ApiClient, type TokenGetter } from "./api";

const TokenContext = createContext<TokenGetter | null>(null);

/** Supplies Clerk session tokens to the API client. */
export function ClerkTokenProvider({ children }: { children: ReactNode }) {
  const { getToken } = useAuth();
  const getter = useMemo<TokenGetter>(() => () => getToken(), [getToken]);
  return <TokenContext.Provider value={getter}>{children}</TokenContext.Provider>;
}

/**
 * Fixed token for automated tests (NEXT_PUBLIC_AUTH_MODE=test). The real API rejects it,
 * so a misconfigured deployment fails closed rather than exposing data.
 */
export function StaticTokenProvider({
  token = "test-token",
  children,
}: {
  token?: string;
  children: ReactNode;
}) {
  const getter = useMemo<TokenGetter>(() => async () => token, [token]);
  return <TokenContext.Provider value={getter}>{children}</TokenContext.Provider>;
}

export function useApi(): ApiClient {
  const getToken = useContext(TokenContext);
  if (!getToken) throw new Error("useApi must be used inside a token provider");
  return useMemo(() => createApiClient(getToken), [getToken]);
}
