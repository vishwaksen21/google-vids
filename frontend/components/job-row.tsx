import Link from "next/link";
import { formatDateTime, percent, shortId } from "@/lib/format";
import { STAGE_LABELS } from "@/lib/status";
import type { JobSummary } from "@/lib/types";
import { StatusBadge } from "./ui/status-badge";

export function ProgressBar({ value, label, className = "" }: { value: number; label: string; className?: string }) {
  const pct = Math.max(0, Math.min(1, value || 0));
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct * 100)}
      className={`h-1.5 w-full overflow-hidden rounded-full bg-gray-100 ${className}`}
    >
      <div className="h-full rounded-full bg-red transition-[width] duration-300" style={{ width: `${pct * 100}%` }} />
    </div>
  );
}

/** A production in a list: campaign, status, current stage and server-computed progress. */
export function JobRow({ job, showCampaign = true }: { job: JobSummary; showCampaign?: boolean }) {
  const stage = job.current_stage ? STAGE_LABELS[job.current_stage] ?? job.current_stage : null;
  return (
    <li>
      <Link
        href={`/jobs/${job.id}`}
        className="group grid grid-cols-1 items-center gap-x-6 gap-y-2 px-5 py-4 transition-colors duration-150 hover:bg-gray-50 sm:grid-cols-[minmax(0,1fr)_200px_auto]"
      >
        <div className="min-w-0">
          <p className="truncate text-[15px] font-semibold text-ink group-hover:text-red-dark">
            {showCampaign ? job.campaign_name ?? "Untitled campaign" : `Production ${shortId(job.id)}`}
          </p>
          <p className="mt-0.5 text-[13px] text-gray">
            {stage ? `Now: ${stage}` : job.status === "COMPLETED" ? "All stages finished" : "Not started"}
            <span aria-hidden className="mx-1.5 text-gray-300">
              |
            </span>
            started by {job.created_by} {formatDateTime(job.created_at)}
            {job.simulation ? (
              <>
                <span aria-hidden className="mx-1.5 text-gray-300">
                  |
                </span>
                simulated
              </>
            ) : null}
          </p>
          {job.error?.message ? <p className="mt-1 line-clamp-2 text-[13px] text-red-dark">{job.error.message}</p> : null}
        </div>
        <div>
          <div className="mb-1 flex justify-between text-[12px] text-gray tabular">
            <span>
              {job.stages_passed} of {job.stages_total} stages
            </span>
            <span>{percent(job.progress)}</span>
          </div>
          <ProgressBar value={job.progress} label={`${job.campaign_name ?? "Production"} progress`} />
        </div>
        <div className="sm:justify-self-end">
          <StatusBadge kind="job" value={job.status} />
        </div>
      </Link>
    </li>
  );
}
