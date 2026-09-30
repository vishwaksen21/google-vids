import type { ReactNode } from "react";
import { JobProvider } from "@/components/job/job-context";
import { JobHeader } from "@/components/job/job-header";

export default async function JobLayout({ children, params }: { children: ReactNode; params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <JobProvider jobId={id}>
      <JobHeader />
      {children}
    </JobProvider>
  );
}
