"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import useSWR, { type SWRConfiguration } from "swr";
import { OperatorRequiredError } from "./api";
import { requestOperatorPrompt } from "./operator";
import type { Approval, BrowserStatus, JobDetail, SystemInfo } from "./types";

export function useSystem() {
  return useSWR<SystemInfo>("system", { refreshInterval: 30000 });
}

export function useBrowserStatus(config?: SWRConfiguration) {
  return useSWR<BrowserStatus>("browser/status", { refreshInterval: 30000, ...config });
}

export function usePendingApprovals() {
  return useSWR<Approval[]>("approvals?status=PENDING", { refreshInterval: 10000 });
}

export function useJob(jobId: string) {
  return useSWR<JobDetail>(`jobs/${jobId}`);
}

/**
 * Runs a server action with pending and error state. Nothing is shown as done until the
 * server has answered. A missing operator name opens the name prompt instead.
 */
export function useAction<A extends unknown[], R>(fn: (...args: A) => Promise<R>) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const inFlight = useRef(false);
  const fnRef = useRef(fn);
  useEffect(() => {
    fnRef.current = fn;
  }, [fn]);

  const run = useCallback(async (...args: A): Promise<R | undefined> => {
    if (inFlight.current) return undefined; // ignore double clicks while one is running
    inFlight.current = true;
    setPending(true);
    setError(null);
    try {
      return await fnRef.current(...args);
    } catch (err) {
      if (err instanceof OperatorRequiredError) requestOperatorPrompt();
      setError(err);
      return undefined;
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }, []);

  const reset = useCallback(() => setError(null), []);
  return { run, pending, error, reset };
}
