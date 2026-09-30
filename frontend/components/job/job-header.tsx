"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSystem } from "@/lib/hooks";
import { percent } from "@/lib/format";
import { STAGE_LABELS, statusMeta, type Tone } from "@/lib/status";
import { STAGE_ORDER, type JobDetail } from "@/lib/types";
import { SimulationBanner, simulationSentence } from "../shell/system-rail";
import { ErrorNotice, Loading } from "../ui/notice";
import { StatusBadge } from "../ui/status-badge";
import { useJobContext } from "./job-context";

const segmentClass: Record<Tone, string> = {
  done: "bg-ink",
  warn: "bg-gray",
  live: "bg-red animate-tally",
  attention: "bg-red",
  fail: "bg-red-deep",
  idle: "bg-gray-200",
  muted: "bg-gray-300",
};

/** 16 segments, one per pipeline stage, in order. The live stage breathes. */
export function StageTally({ job }: { job: JobDetail }) {
  const byStage = new Map(job.stages.map((s) => [s.stage, s]));
  const current = job.current_stage ? STAGE_ORDER.indexOf(job.current_stage) : -1;
  return (
    <div>
      <ol aria-label="Pipeline stages" className="grid grid-cols-16 gap-[3px]">
        {STAGE_ORDER.map((stage, i) => {
          const s = byStage.get(stage);
          const meta = statusMeta("stage", s?.status ?? "PENDING");
          return (
            <li key={stage} className="group relative">
              <span aria-hidden className={`block h-2 rounded-[2px] ${segmentClass[meta.tone]} ${i === current ? "ring-2 ring-red ring-offset-1" : ""}`} title={`${i + 1}. ${STAGE_LABELS[stage]}: ${meta.label}`} />
              <span className="visually-hidden">
                {i + 1}. {STAGE_LABELS[stage]}: {meta.label}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

const TABS = [
  { href: "", label: "Progress" },
  { href: "/plan", label: "Plan" },
  { href: "/storyboard", label: "Storyboard" },
  { href: "/scenes", label: "Scenes" },
  { href: "/qa", label: "Checks" },
  { href: "/delivery", label: "Video" },
];

const CONNECTION_TEXT: Record<string, string> = {
  connecting: "Connecting to live updates",
  open: "Live",
  reconnecting: "Reconnecting",
  closed: "Not live",
};

export function JobHeader() {
  const { jobId, job, error, refresh, events } = useJobContext();
  const pathname = usePathname() || "";
  const { data: sys } = useSystem();
  const base = `/jobs/${jobId}`;

  if (error && !job) {
    return <ErrorNotice error={error} title="This production could not be loaded" onRetry={() => void refresh()} className="mb-8" />;
  }
  if (!job) return <Loading label="Loading production" lines={3} />;

  const currentIndex = job.current_stage ? STAGE_ORDER.indexOf(job.current_stage) + 1 : null;
  const pending = job.approvals.filter((a) => a.status === "PENDING").length;
  const live = events.connection === "open";

  return (
    <>
      {job.simulation && !sys?.simulation ? (
        <div className="-mx-4 -mt-8 mb-6 sm:-mx-6 lg:-mx-8">
          <SimulationBanner sentence={`${simulationSentence({ google_vids_mode: job.adapter === "mock" ? "mock" : undefined, ai_provider: job.ai_provider })}. This production was simulated.`} />
        </div>
      ) : null}
      <div className="mb-6">
        <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-gray">
          <Link href={`/campaigns/${job.campaign_id}`} className="font-medium text-red-dark underline-offset-2 hover:underline">
            {job.campaign_name ?? "Campaign"}
          </Link>
          <span aria-hidden>/</span>
          <span>Production started by {job.created_by}</span>
          {job.simulation ? <StatusBadge kind="stage" value="SKIPPED" label="Simulated run" size="sm" /> : null}
        </div>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-[28px] leading-tight font-bold tracking-[-0.01em] text-ink sm:text-[32px]">
              {job.current_stage && job.status !== "COMPLETED" ? STAGE_LABELS[job.current_stage] : statusMeta("job", job.status).label}
            </h1>
            <p className="mt-1 text-[15px] text-gray tabular">
              {currentIndex ? `Stage ${currentIndex} of ${job.stages_total}. ` : ""}
              {job.stages_passed} of {job.stages_total} stages passed ({percent(job.progress)}).
              {pending ? ` ${pending} decision${pending === 1 ? "" : "s"} waiting.` : ""}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1.5 text-[13px] text-gray" role="status" aria-live="polite">
              <span aria-hidden className={`size-2 rounded-full ${live ? "bg-red animate-tally" : "bg-gray-300"}`} />
              {CONNECTION_TEXT[events.connection]}
            </span>
            <StatusBadge kind="job" value={job.status} />
          </div>
        </div>
        <div className="mt-5">
          <StageTally job={job} />
        </div>
      </div>
      <nav aria-label="Production" className="mb-8 border-b border-gray-200">
        <ul className="-mb-px flex gap-1 overflow-x-auto">
          {TABS.map((t) => {
            const href = `${base}${t.href}`;
            const active = t.href === "" ? pathname === base : pathname.startsWith(href);
            return (
              <li key={t.label}>
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={`flex h-11 items-center border-b-[3px] px-3 text-[14px] font-medium whitespace-nowrap transition-colors duration-150 ${
                    active ? "border-red text-red-dark" : "border-transparent text-gray hover:border-gray-300 hover:text-ink"
                  }`}
                >
                  {t.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </>
  );
}
