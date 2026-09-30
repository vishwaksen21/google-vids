"use client";

import { friendlyError } from "@/lib/friendly-error";

import { AnimatePresence, motion } from "framer-motion";
import { ChevronDown } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { elapsed, formatTime } from "@/lib/format";
import { humanise, STAGE_LABELS, statusMeta } from "@/lib/status";
import type { JobDetail, StageAttempt, StageOut } from "@/lib/types";
import { ErrorNotice, Loading } from "../ui/notice";
import { StatusBadge } from "../ui/status-badge";
import { ValidationList, ValidationSummary } from "../validation-list";

function SceneCounts({ stage, jobId }: { stage: StageOut; jobId: string }) {
  const counts = stage.progress?.scene_counts;
  if (!counts) return null;
  const total = stage.progress?.scene_total;
  return (
    <p className="mt-1 text-[13px] text-gray tabular">
      {total != null ? `${total} scenes: ` : ""}
      {Object.entries(counts)
        .map(([k, v]) => `${v} ${statusMeta("scene", k).label.toLowerCase()}`)
        .join(", ")}
      {stage.progress?.active_scene ? (
        <>
          {". Working on "}
          <Link className="font-medium text-red-dark underline-offset-2 hover:underline" href={`/jobs/${jobId}/scenes/${stage.progress.active_scene}`}>
            {stage.progress.active_scene}
          </Link>
        </>
      ) : null}
    </p>
  );
}

function OtherProgress({ stage }: { stage: StageOut }) {
  const p = stage.progress;
  if (!p || p.scene_counts) return null;
  if (typeof p.ops_total === "number") {
    return (
      <p className="mt-1 text-[13px] text-gray tabular">
        {String(p.ops_verified ?? 0)} of {String(p.ops_total)} timeline edits verified
        {p.active_op ? `; now ${String(p.active_op)}` : ""}
      </p>
    );
  }
  return null;
}

function StageDetail({ jobId, stage }: { jobId: string; stage: string }) {
  const { data, error, mutate } = useSWR<StageAttempt[]>(`jobs/${jobId}/stages/${stage}`);
  if (error) return <ErrorNotice error={error} onRetry={() => mutate()} />;
  if (!data) return <Loading label="Loading validation results" lines={2} />;
  if (data.length === 0) return <p className="text-[14px] text-gray">This stage has not run yet.</p>;
  return (
    <ol className="space-y-5">
      {[...data].reverse().map((a) => (
        <li key={a.id}>
          <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1">
            <h4 className="text-[14px] font-semibold text-ink">Attempt {a.attempt}</h4>
            <StatusBadge kind="stage" value={a.status} size="sm" />
            <span className="text-[12.5px] text-gray tabular">
              {formatTime(a.started_at)}
              {a.finished_at ? ` to ${formatTime(a.finished_at)} (${elapsed(a.started_at, a.finished_at)})` : ""}
            </span>
            {a.next_permitted_action ? <span className="text-[12.5px] text-gray">Next: {humanise(a.next_permitted_action)}</span> : null}
          </div>
          {a.error?.message ? (
            <p className="mb-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-deep">
              {friendlyError(a.error)}
              {a.error.code ? <span className="ml-1 text-[12px] text-gray">({a.error.code})</span> : null}
            </p>
          ) : null}
          {a.validations.length ? (
            <div className="mb-2">
              <ValidationSummary list={a.validations} />
            </div>
          ) : null}
          <ValidationList list={a.validations} emptyText="No validation results were recorded for this attempt." />
        </li>
      ))}
    </ol>
  );
}

/** The 16 stages as a running order. Each row expands to its attempts and validation results. */
export function StageRundown({ job }: { job: JobDetail }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <ol className="divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white">
      {job.stages.map((s, i) => {
        const isCurrent = s.stage === job.current_stage;
        const expanded = open === s.stage;
        const panelId = `stage-${s.stage}`;
        return (
          <li key={s.stage} className={`relative ${isCurrent ? "bg-red-50/60" : ""}`} data-stage={s.stage} data-status={s.status}>
            {isCurrent ? <span aria-hidden className="absolute inset-y-0 left-0 w-[3px] bg-red" /> : null}
            <button
              type="button"
              aria-expanded={expanded}
              aria-controls={panelId}
              onClick={() => setOpen(expanded ? null : s.stage)}
              className="grid w-full grid-cols-[40px_minmax(0,1fr)_auto] items-start gap-x-3 px-4 py-3.5 text-left hover:bg-gray-50 sm:grid-cols-[48px_minmax(0,1fr)_120px_236px]"
            >
              <span className={`pt-0.5 text-[20px] leading-none font-semibold tabular ${isCurrent ? "text-red" : s.status === "PENDING" ? "text-gray-300" : "text-gray"}`}>
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="min-w-0">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-[15px] font-semibold text-ink">{STAGE_LABELS[s.stage] ?? s.stage}</span>
                  {isCurrent ? <span className="text-[12px] font-medium text-red-dark">current stage</span> : null}
                </span>
                {s.error?.message ? (
                  <span className="mt-1 block text-[13px] text-red-dark">
                    {friendlyError(s.error)}
                    {s.error.code ? <span className="ml-1 text-[12px] text-gray">({s.error.code})</span> : null}
                  </span>
                ) : null}
                <SceneCounts stage={s} jobId={job.id} />
                <OtherProgress stage={s} />
              </span>
              <span className="hidden text-[12.5px] text-gray tabular sm:block">
                {s.attempts ? (
                  <>
                    {s.attempts} attempt{s.attempts === 1 ? "" : "s"}
                    <br />
                    {s.failures} failed check{s.failures === 1 ? "" : "s"}
                  </>
                ) : (
                  "Not run"
                )}
              </span>
              <span className="flex items-center justify-end gap-2">
                <StatusBadge kind="stage" value={s.status} size="sm" />
                <ChevronDown aria-hidden className={`size-4 text-gray transition-transform duration-200 ${expanded ? "rotate-180" : ""}`} />
                <span className="visually-hidden">{expanded ? "Hide" : "Show"} validation results</span>
              </span>
            </button>
            <AnimatePresence initial={false}>
              {expanded ? (
                <motion.div
                  id={panelId}
                  key="panel"
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.22, ease: [0.2, 0, 0, 1] }}
                  className="overflow-hidden"
                >
                  <div className="border-t border-gray-100 px-4 py-4 sm:pl-[76px]">
                    <p className="mb-3 text-[12.5px] text-gray sm:hidden tabular">
                      {s.attempts} attempt{s.attempts === 1 ? "" : "s"}, {s.failures} failed check{s.failures === 1 ? "" : "s"}
                    </p>
                    <StageDetail jobId={job.id} stage={s.stage} />
                  </div>
                </motion.div>
              ) : null}
            </AnimatePresence>
          </li>
        );
      })}
    </ol>
  );
}
