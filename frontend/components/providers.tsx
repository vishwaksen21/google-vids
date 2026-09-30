"use client";

import { MotionConfig } from "framer-motion";
import type { ReactNode } from "react";
import { SWRConfig } from "swr";
import { ApiError, fetcher } from "@/lib/api";
import { OperatorPrompt } from "./operator-prompt";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <SWRConfig
      value={{
        fetcher,
        revalidateOnFocus: true,
        dedupingInterval: 1000,
        keepPreviousData: true,
        // A 404 or 409 is an answer, not a blip: don't hammer the server with retries.
        shouldRetryOnError: (err) => !(err instanceof ApiError) || err.status >= 500 || err.status === 0,
        errorRetryCount: 3,
      }}
    >
      <MotionConfig reducedMotion="user">
        {children}
        <OperatorPrompt />
      </MotionConfig>
    </SWRConfig>
  );
}
