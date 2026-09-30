"use client";

import Link from "next/link";
import { Plus } from "lucide-react";
import useSWR from "swr";
import { ApprovalCard } from "@/components/approval-card";
import { JobRow } from "@/components/job-row";
import { GoogleVidsCard } from "@/components/system-status";
import { LinkButton } from "@/components/ui/button";
import { EmptyState, ErrorNotice, Loading } from "@/components/ui/notice";
import { PageHeader, Section } from "@/components/ui/section";
import { StatusBadge } from "@/components/ui/status-badge";
import { formatDateTime } from "@/lib/format";
import { usePendingApprovals } from "@/lib/hooks";
import { friendlyError } from "@/lib/friendly-error";
import { STAGE_LABELS } from "@/lib/status";
import type { Campaign, JobSummary } from "@/lib/types";

export function Dashboard() {
  const active = useSWR<JobSummary[]>("jobs?active=true&limit=50", { refreshInterval: 5000 });
  const recent = useSWR<JobSummary[]>("jobs?limit=50", { refreshInterval: 15000 });
  const approvals = usePendingApprovals();
  const campaigns = useSWR<Campaign[]>("campaigns");

  const needsPerson = (recent.data ?? []).filter((j) => j.status === "FAILED" || j.status === "WAITING_FOR_HUMAN");
  const approvalJobIds = new Set((approvals.data ?? []).map((a) => a.job_id));
  const jobIssues = needsPerson.filter((j) => j.status === "FAILED" || !approvalJobIds.has(j.id));
  const attentionCount = (approvals.data?.length ?? 0) + jobIssues.length;

  return (
    <>
      <PageHeader
        title="Home"
        lead="Start a campaign, approve what is waiting for you, and follow videos being made."
        actions={
          <LinkButton href="/campaigns/new" variant="primary" icon={<Plus aria-hidden className="size-4" />}>
            New campaign
          </LinkButton>
        }
      />

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 space-y-8">
          <Section
            title="Waiting for you"
            description={
              approvals.data && recent.data && attentionCount
                ? `${attentionCount} item${attentionCount === 1 ? "" : "s"} need${attentionCount === 1 ? "s" : ""} a decision or a look.`
                : undefined
            }
          >
            <ErrorNotice error={approvals.error} onRetry={() => approvals.mutate()} className="mb-3" />
            <ErrorNotice error={recent.error} onRetry={() => recent.mutate()} className="mb-3" />
            {!approvals.data && !approvals.error ? <Loading label="Loading approvals" lines={2} /> : null}
            {approvals.data && recent.data && attentionCount === 0 ? (
              <p className="text-[15px] text-ink">You&apos;re all caught up. Nothing needs your approval.</p>
            ) : null}
            <div className="space-y-3">
              {(approvals.data ?? []).map((a) => (
                <ApprovalCard key={a.id} approval={a} showJobLink compact onDecided={() => { void approvals.mutate(); void active.mutate(); void recent.mutate(); }} />
              ))}
              {jobIssues.map((j) => (
                <article key={j.id} className="rounded-xl border border-red-200 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className="text-[16px] font-semibold text-ink">
                        <Link href={`/jobs/${j.id}`} className="hover:text-red-dark">
                          {j.campaign_name ?? "Production"}
                        </Link>
                      </h3>
                      <p className="text-[13px] text-gray">
                        {j.current_stage ? `Stopped at ${STAGE_LABELS[j.current_stage] ?? j.current_stage}` : "Stopped"}
                        {j.error?.code ? ` (${j.error.code})` : ""}
                      </p>
                    </div>
                    <StatusBadge kind="job" value={j.status} size="sm" />
                  </div>
                  {j.error?.message ? <p className="mt-2 text-[14px] text-red-deep">{friendlyError(j.error)}</p> : null}
                  <p className="mt-2 text-[13px]">
                    <Link href={`/jobs/${j.id}`} className="font-medium text-red-dark underline-offset-2 hover:underline">
                      Open it to retry or cancel
                    </Link>
                  </p>
                </article>
              ))}
            </div>
          </Section>

          {active.data && active.data.length > 0 ? (
            <Section title="In progress" description="Videos being planned or made right now." className="overflow-hidden">
              <div className="-mx-5 -my-4">
                <ul className="divide-y divide-gray-100">
                  {active.data.map((j) => (
                    <JobRow key={j.id} job={j} />
                  ))}
                </ul>
              </div>
            </Section>
          ) : null}
          <ErrorNotice error={active.error} onRetry={() => active.mutate()} />

          <Section
            title="Your campaigns"
            actions={
              <Link href="/campaigns" className="text-[14px] font-medium text-red-dark underline-offset-2 hover:underline">
                See all
              </Link>
            }
            className="overflow-hidden"
          >
            <div className="-mx-5 -my-4">
              <ErrorNotice error={campaigns.error} onRetry={() => campaigns.mutate()} className="m-5" />
              {!campaigns.data && !campaigns.error ? (
                <div className="px-5 py-4">
                  <Loading label="Loading campaigns" />
                </div>
              ) : null}
              {campaigns.data && campaigns.data.length === 0 ? (
                <div className="p-5">
                  <EmptyState title="No campaigns yet" action={<LinkButton href="/campaigns/new" variant="primary">New campaign</LinkButton>}>
                    Describe the video you need; it takes about a minute.
                  </EmptyState>
                </div>
              ) : null}
              {campaigns.data && campaigns.data.length > 0 ? (
                <ul className="divide-y divide-gray-100">
                  {campaigns.data.slice(0, 6).map((c) => (
                    <li key={c.id}>
                      <Link href={`/campaigns/${c.id}`} className="group flex flex-wrap items-center justify-between gap-3 px-5 py-3.5 hover:bg-gray-50">
                        <div className="min-w-0">
                          <p className="truncate text-[15px] font-medium text-ink group-hover:text-red-dark">{c.name}</p>
                          <p className="text-[13px] text-gray">
                            Updated {formatDateTime(c.updated_at)}
                          </p>
                        </div>
                        {c.latest_job ? <StatusBadge kind="job" value={c.latest_job.status} size="sm" /> : <span className="text-[13px] text-gray">Not produced yet</span>}
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          </Section>
        </div>

        <aside aria-label="Google Vids" className="min-w-0">
          <GoogleVidsCard />
        </aside>
      </div>
    </>
  );
}
