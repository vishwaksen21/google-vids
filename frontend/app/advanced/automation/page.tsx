import type { Metadata } from "next";
import { Suspense } from "react";
import { AutomationPage } from "./automation-view";

export const metadata: Metadata = { title: "Automation details" };

export default function Page() {
  return (
    <Suspense>
      <AutomationPage />
    </Suspense>
  );
}
