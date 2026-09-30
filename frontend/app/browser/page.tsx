import type { Metadata } from "next";
import { Suspense } from "react";
import { BrowserPage } from "./browser-view";

export const metadata: Metadata = { title: "Google account" };

export default function Page() {
  return (
    <Suspense>
      <BrowserPage />
    </Suspense>
  );
}
