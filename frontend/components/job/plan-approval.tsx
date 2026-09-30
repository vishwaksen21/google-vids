"use client";

import { ApprovalCard } from "../approval-card";
import { useJobContext } from "./job-context";

/** The pending PLAN approval for this job, decided in place. Renders nothing otherwise. */
export function PendingApprovals({ kinds }: { kinds: string[] }) {
  const { job, refresh } = useJobContext();
  const pending = (job?.approvals ?? []).filter((a) => a.status === "PENDING" && kinds.includes(a.kind));
  if (!pending.length) return null;
  return (
    <div className="mb-8 space-y-3">
      {pending.map((a) => (
        <ApprovalCard key={a.id} approval={a} onDecided={() => void refresh()} />
      ))}
    </div>
  );
}
