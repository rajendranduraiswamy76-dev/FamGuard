"use client";

import { createContext, useContext, type ReactNode } from "react";

import { useApi } from "./auth";
import { useAsync, type AsyncState } from "./hooks";
import type { Me } from "./types";

const VaultContext = createContext<AsyncState<Me> | null>(null);

/** Loads the signed-in member, family and roster once for the whole app. */
export function VaultProvider({ children }: { children: ReactNode }) {
  const api = useApi();
  const state = useAsync(() => api.me(), [api]);
  return <VaultContext.Provider value={state}>{children}</VaultContext.Provider>;
}

export function useVault(): AsyncState<Me> {
  const value = useContext(VaultContext);
  if (!value) throw new Error("useVault must be used inside VaultProvider");
  return value;
}
