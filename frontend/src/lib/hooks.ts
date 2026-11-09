"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "./api";

export interface AsyncState<T> {
  data: T | undefined;
  error: ApiError | Error | undefined;
  loading: boolean;
  reload: () => void;
  setData: (updater: (current: T | undefined) => T | undefined) => void;
}

interface Settled<T> {
  version: number;
  data: T | undefined;
  error: ApiError | Error | undefined;
}

/** Minimal data hook: runs `load` on mount and on reload(); stale responses are ignored. */
export function useAsync<T>(load: () => Promise<T>, deps: readonly unknown[]): AsyncState<T> {
  const [version, setVersion] = useState(0);
  const [settled, setSettled] = useState<Settled<T>>({
    version: -1,
    data: undefined,
    error: undefined,
  });

  useEffect(() => {
    let active = true;
    load()
      .then((data) => {
        if (active) setSettled({ version, data, error: undefined });
      })
      .catch((err: unknown) => {
        const error = err instanceof Error ? err : new Error(String(err));
        if (active) setSettled((prev) => ({ version, data: prev.data, error }));
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- callers pass explicit deps
  }, [...deps, version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  const setData = useCallback(
    (updater: (current: T | undefined) => T | undefined) =>
      setSettled((prev) => ({ ...prev, data: updater(prev.data) })),
    [],
  );
  return {
    data: settled.data,
    error: settled.error,
    loading: settled.version !== version,
    reload,
    setData,
  };
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.isUnavailable) {
      return "Waking up the vault… the server is starting, please retry shortly.";
    }
    return error.message;
  }
  return error instanceof Error ? error.message : "Something went wrong";
}
