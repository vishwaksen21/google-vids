import type { Metadata } from "next";
import { Suspense } from "react";
import { LoginPage } from "./login-view";

export const metadata: Metadata = { title: "Google sign-in" };

export default function Page() {
  return (
    <Suspense>
      <LoginPage />
    </Suspense>
  );
}
