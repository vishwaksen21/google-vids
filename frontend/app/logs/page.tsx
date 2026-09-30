import type { Metadata } from "next";
import { Suspense } from "react";
import { LogsPage } from "./logs-view";

export const metadata: Metadata = { title: "Automation logs" };

export default function Page() {
  return (
    <Suspense>
      <LogsPage />
    </Suspense>
  );
}
