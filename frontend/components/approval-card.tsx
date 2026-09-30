"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { apiRequest } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import { APPROVAL_KIND_LABELS } from "@/lib/status";
import type { Approval } from "@/lib/types";
import { Button, LinkButton } from "./ui/button";
import { ErrorNotice } from "./ui/notice";
import { StatusBadge } from "./ui/status-badge";

function interventionIntent(a: Approval): string | null {
  const err = (a.payload?.error ?? null) as { details?: { intent_id?: string } } | null;
  return err?.details?.intent_id ?? null;
}

const APPROVE_LABEL: Record<string, string> = {
  PLAN: "Approve plan",
  COMPLIANCE: "Approve for delivery",
  SUBMISSION: "Approve submission",
  INTERVENTION: "Retry the stage",
};
const REJECT_LABEL: Record<string, string> = {
  PLAN: "Reject plan",
  COMPLIANCE: "Reject",
  SUBMISSION: "Reject",
  INTERVENTION: "Fail the production",
};

/**
 * One approval request. Pending ones can be decided here (except scene reviews, which are
 * decided on the scene review page with the evidence in view).
 */
export function ApprovalCard({ approval, onDecided, showJobLink = false, compact = false }: { approval: Approval; onDecided?: () => void; showJobLink?: boolean; compact?: boolean }) {
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string>();
  const [intent, setIntent] = useState<"" | "happened" | "not_happened">("");
  const intentId = approval.kind === "INTERVENTION" ? interventionIntent(approval) : null;
  const pending = approval.status === "PENDING";
  const sceneKey = approval.kind === "SCENE_REVIEW" ? (approval.payload?.scene_key as string | undefined) : undefined;

  const decide = useAction(async (approve: boolean) => {
    const body: Record<string, unknown> = { approve, reason: reason.trim() };
    if (intentId && approve && intent) body.intent_resolution = intent;
    const res = await apiRequest<Approval>(`approvals/${approval.id}/decision`, { method: "POST", body });
    onDecided?.();
    return res;
  });

  const submit = (approve: boolean) => {
    if (!reason.trim()) {
      setReasonError("Give a reason. It is recorded with your decision.");
      return;
    }
    setReasonError(undefined);
    void decide.run(approve);
  };

  return (
    <article
      className={`rounded-xl border bg-white ${pending ? "border-red-200" : "border-gray-200"} ${compact ? "p-4" : "p-5"}`}
      aria-label={`${APPROVAL_KIND_LABELS[approval.kind] ?? approval.kind}: ${approval.title}`}
      data-approval-kind={approval.kind}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[13px] text-gray">
            {APPROVAL_KIND_LABELS[approval.kind] ?? approval.kind}
            <span className="mx-1.5 text-gray-300" aria-hidden>
              |
            </span>
            requested {formatDateTime(approval.created_at)}
          </p>
          <h3 className="mt-0.5 text-[16px] font-semibold text-ink">{approval.title}</h3>
        </div>
        <StatusBadge kind="approval" value={approval.status} size="sm" />
      </div>
      {approval.summary ? <p className="mt-2 max-w-prose text-[14px] leading-relaxed text-gray-700">{approval.summary}</p> : null}

      {showJobLink ? (
        <p className="mt-2 text-[13px]">
          <Link className="font-medium text-red-dark underline-offset-2 hover:underline" href={`/jobs/${approval.job_id}`}>
            Open the production
          </Link>
        </p>
      ) : null}

      {!pending && approval.decided_by ? (
        <p className="mt-3 text-[13px] text-gray">
          Decided by {approval.decided_by} on {formatDateTime(approval.decided_at)}
          {typeof approval.decision?.reason === "string" ? `: “${approval.decision.reason}”` : ""}
        </p>
      ) : null}

      {pending && approval.kind === "SCENE_REVIEW" ? (
        <div className="mt-4">
          <LinkButton variant="primary" size="sm" href={`/jobs/${approval.job_id}/scenes/${sceneKey ?? ""}`}>
            Review scene {sceneKey}
          </LinkButton>
        </div>
      ) : null}

      {pending && approval.kind !== "SCENE_REVIEW" ? (
        <div className="mt-4 space-y-3">
          {intentId ? (
            <fieldset className="rounded-lg border border-gray-200 px-4 py-3">
              <legend className="px-1 text-[14px] font-medium text-ink">Did the interrupted action happen in Google Vids?</legend>
              <p className="mb-2 text-[13px] text-gray">Check the Vids project first. Answering wrongly can spend a generation twice.</p>
              <div className="flex flex-wrap gap-4 text-[14px]">
                {(
                  [
                    ["", "Don't say"],
                    ["happened", "It happened"],
                    ["not_happened", "It did not happen"],
                  ] as const
                ).map(([value, label]) => (
                  <label key={value} className="flex items-center gap-2">
                    <input type="radio" name={`intent-${approval.id}`} value={value} checked={intent === value} onChange={() => setIntent(value)} />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>
          ) : null}
          <div>
            <label htmlFor={reasonId} className="mb-1.5 block text-[14px] font-medium text-ink">
              Reason for your decision
            </label>
            <textarea
              id={reasonId}
              rows={2}
              value={reason}
              maxLength={2000}
              onChange={(e) => setReason(e.target.value)}
              aria-invalid={reasonError ? true : undefined}
              aria-describedby={reasonError ? `${reasonId}-err` : undefined}
              className={`block w-full rounded-md border bg-white px-3 py-2 text-[15px] focus:border-red focus:outline-none ${reasonError ? "border-red" : "border-gray-300"}`}
            />
            {reasonError ? (
              <p id={`${reasonId}-err`} className="mt-1.5 text-[13px] font-medium text-red-dark">
                {reasonError}
              </p>
            ) : null}
          </div>
          <ErrorNotice error={decide.error} />
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" loading={decide.pending} onClick={() => submit(true)}>
              {APPROVE_LABEL[approval.kind] ?? "Approve"}
            </Button>
            <Button variant="danger" disabled={decide.pending} onClick={() => submit(false)}>
              {REJECT_LABEL[approval.kind] ?? "Reject"}
            </Button>
          </div>
        </div>
      ) : null}
    </article>
  );
}
