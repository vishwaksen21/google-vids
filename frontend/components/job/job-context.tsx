"use client";

import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";
import { useSWRConfig } from "swr";
import { useJob } from "@/lib/hooks";
import { throttle, useJobEvents, type EventsState } from "@/lib/job-events";
import { TERMINAL_JOB_STATUSES } from "@/lib/status";
import type { JobDetail } from "@/lib/types";

interface JobContextValue {
  jobId: string;
  job: JobDetail | undefined;
  error: unknown;
  refresh: () => Promise<unknown>;
  events: EventsState;
}

const JobContext = createContext<JobContextValue | null>(null);

export function useJobContext(): JobContextValue {
  const ctx = useContext(JobContext);
  if (!ctx) throw new Error("useJobContext must be used inside a job page");
  return ctx;
}

/**
 * Holds the job for every job sub-page and keeps it live: one SSE stream per open job,
 * and each event (throttled) revalidates every `jobs/{id}…` resource from the server.
 */
export function JobProvider({ jobId, children }: { jobId: string; children: ReactNode }) {
  const { data: job, error, mutate: mutateJob } = useJob(jobId);
  const { mutate } = useSWRConfig();
  const prefix = `jobs/${jobId}`;

  const revalidate = useMemo(
    () =>
      throttle(() => {
        void mutate((key) => typeof key === "string" && (key === prefix || key.startsWith(`${prefix}/`) || key.startsWith("approvals")));
      }, 1500),
    [mutate, prefix],
  );
  useEffect(() => () => revalidate.cancel(), [revalidate]);

  // Stop streaming once the job can no longer change (completed or cancelled).
  const terminal = job ? TERMINAL_JOB_STATUSES.has(job.status) : false;
  const events = useJobEvents(jobId, { onEvent: revalidate, enabled: !terminal && !error });

  const value = useMemo<JobContextValue>(() => ({ jobId, job, error, refresh: () => mutateJob(), events }), [jobId, job, error, mutateJob, events]);
  return <JobContext.Provider value={value}>{children}</JobContext.Provider>;
}
