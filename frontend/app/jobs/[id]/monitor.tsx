"use client";

import { friendlyError } from "@/lib/friendly-error";

import Link from "next/link";
import { Ban, Pause, Play, RotateCcw } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { ApprovalCard } from "@/components/approval-card";
import { useJobContext } from "@/components/job/job-context";
import { StageRundown } from "@/components/job/stage-rundown";
import { ProgressBar } from "@/components/job-row";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { SelectField } from "@/components/ui/field";
import { ErrorNotice, Notice } from "@/components/ui/notice";
import { KeyValues, Section } from "@/components/ui/section";
import { apiRequest } from "@/lib/api";
import { formatDateTime, formatTime } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import { STAGE_LABELS, statusMeta } from "@/lib/status";
import { STAGE_ORDER, type AuditEvent, type JobDetail } from "@/lib/types";

function ActivityFeed({ jobId }: { jobId: string }) {
  const { events, job } = useJobContext();
  // Completed or cancelled jobs have no stream; read the stored audit trail instead.
  const fallback = useSWR<AuditEvent[]>(events.events.length === 0 && job ? `jobs/${jobId}/audit?limit=1000` : null);
  const list = (events.events.length ? events.events : fallback.data ?? []).slice(-40).reverse();
  return (
    <Section
      title="Activity"
      description="Recorded by the backend as it happens."
      actions={
        <Link href={`/logs?job=${jobId}`} className="text-[13px] font-medium text-red-dark underline-offset-2 hover:underline">
          Full log
        </Link>
      }
    >
      {events.error && events.connection !== "open" ? <p className="mb-2 text-[13px] text-gray">{events.error}</p> : null}
      {list.length === 0 ? (
        <p className="text-[14px] text-gray">No activity recorded yet.</p>
      ) : (
        <ol className="max-h-[520px] space-y-3 overflow-y-auto pr-1" aria-live="off">
          {list.map((e) => (
            <li key={e.id} className="grid grid-cols-[64px_minmax(0,1fr)] gap-2 text-[13px]">
              <time dateTime={e.created_at} className="text-gray tabular">
                {formatTime(e.created_at)}
              </time>
              <div className="min-w-0">
                <p className="break-words text-ink">{e.message}</p>
                <p className="text-[12px] text-gray">
                  {e.type}
                  {e.actor !== "system" ? ` by ${e.actor}` : ""}
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Section>
  );
}

function Controls({ job, onUpdated }: { job: JobDetail; onUpdated: (j: JobDetail) => void }) {
  const [cancelOpen, setCancelOpen] = useState(false);
  const [retryOpen, setRetryOpen] = useState(false);
  const [retryStage, setRetryStage] = useState("");
  const [lastResult, setLastResult] = useState<string | null>(null);

  const control = useAction(async (action: "pause" | "resume" | "cancel" | "retry") => {
    setLastResult(null);
    const body = action === "retry" ? { stage: retryStage || null } : undefined;
    const updated = await apiRequest<JobDetail>(`jobs/${job.id}/${action}`, { method: "POST", body: body ?? {} });
    onUpdated(updated);
    setCancelOpen(false);
    setRetryOpen(false);
    setLastResult(`The server reports the production as ${statusMeta("job", updated.status).label.toLowerCase()}${updated.control ? ` (${updated.control.toLowerCase()} requested)` : ""}.`);
    return updated;
  });

  const s = job.status;
  const canPause = (s === "RUNNING" || s === "QUEUED" || s === "WAITING_FOR_HUMAN") && job.control !== "PAUSE";
  const canResume = s === "PAUSED" || job.control === "PAUSE";
  const canCancel = s !== "COMPLETED" && s !== "CANCELLED";
  const canRetry = s === "FAILED" || s === "WAITING_FOR_HUMAN" || s === "PAUSED";

  if (s === "COMPLETED" || s === "CANCELLED") {
    return (
      <Section title="Controls">
        <p className="text-[14px] text-gray">
          This production is {statusMeta("job", s).label.toLowerCase()}. It can no longer be paused, retried or cancelled. Start a new production from the campaign to make another version.
        </p>
      </Section>
    );
  }

  return (
    <Section title="Controls" description="Pause and cancel take effect at the next safe checkpoint.">
      <div className="flex flex-wrap gap-2">
        {canResume ? (
          <Button size="sm" variant="primary" icon={<Play aria-hidden className="size-4" />} loading={control.pending} onClick={() => void control.run("resume")}>
            Resume
          </Button>
        ) : (
          <Button size="sm" icon={<Pause aria-hidden className="size-4" />} disabled={!canPause || control.pending} onClick={() => void control.run("pause")}>
            Pause
          </Button>
        )}
        <Button size="sm" icon={<RotateCcw aria-hidden className="size-4" />} disabled={!canRetry || control.pending} onClick={() => { control.reset(); setRetryOpen(true); }}>
          Retry
        </Button>
        <Button size="sm" variant="danger" icon={<Ban aria-hidden className="size-4" />} disabled={!canCancel || control.pending} onClick={() => { control.reset(); setCancelOpen(true); }}>
          Cancel production
        </Button>
      </div>
      {job.control ? <p className="mt-3 text-[13px] text-gray">{job.control === "PAUSE" ? "Pause" : "Cancel"} requested; waiting for the worker to reach a checkpoint.</p> : null}
      {!canRetry ? <p className="mt-3 text-[13px] text-gray">Retry is available when the production has failed, is paused, or is waiting for a person.</p> : null}
      {lastResult ? <p className="mt-3 text-[13px] text-ink" role="status">{lastResult}</p> : null}
      {!cancelOpen && !retryOpen ? <ErrorNotice error={control.error} className="mt-3" /> : null}

      <Dialog
        open={cancelOpen}
        onClose={() => setCancelOpen(false)}
        title="Cancel this production?"
        description="The worker stops at its next checkpoint. Cancelled productions cannot be resumed or retried; start a new production to try again. Generations already spent are not refunded."
        footer={
          <>
            <Button onClick={() => setCancelOpen(false)}>Keep running</Button>
            <Button variant="primary" loading={control.pending} onClick={() => void control.run("cancel")}>
              Cancel production
            </Button>
          </>
        }
      >
        <ErrorNotice error={control.error} />
      </Dialog>

      <Dialog
        open={retryOpen}
        onClose={() => setRetryOpen(false)}
        title="Retry the production"
        description="The chosen stage and everything after it run again. Earlier attempts are kept in the history."
        footer={
          <>
            <Button onClick={() => setRetryOpen(false)}>Cancel</Button>
            <Button variant="primary" loading={control.pending} onClick={() => void control.run("retry")}>
              Retry
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <SelectField label="Retry from" value={retryStage} onChange={(e) => setRetryStage(e.target.value)} hint="Planning stages cannot be redone once scene production has started.">
            <option value="">The first unfinished stage</option>
            {STAGE_ORDER.map((st, i) => (
              <option key={st} value={st}>
                {i + 1}. {STAGE_LABELS[st]}
              </option>
            ))}
          </SelectField>
          <ErrorNotice error={control.error} />
        </div>
      </Dialog>
    </Section>
  );
}

function NowPanel({ job }: { job: JobDetail }) {
  const production = job.stages.find((s) => s.stage === "SCENE_PRODUCTION");
  const counts = production?.progress?.scene_counts;
  const total = production?.progress?.scene_total;
  const b = job.budget;
  const project = job.vids_project;
  const projectUrl = typeof project?.url === "string" && project.url.startsWith("https://") ? project.url : null;
  return (
    <Section title="Now">
      <div className="space-y-5">
        <div>
          <p className="text-[13px] text-gray">Latest recorded action</p>
          <p className="mt-0.5 text-[15px] text-ink">{job.current_action ?? "Nothing recorded yet"}</p>
        </div>
        <KeyValues
          columns={2}
          items={[
            {
              label: "Active scene",
              value: job.active_scene ? (
                <Link className="font-medium text-red-dark underline-offset-2 hover:underline" href={`/jobs/${job.id}/scenes/${job.active_scene}`}>
                  {job.active_scene}
                </Link>
              ) : (
                "None"
              ),
            },
            { label: "Current stage", value: job.current_stage ? STAGE_LABELS[job.current_stage] : "None" },
            { label: "Started", value: formatDateTime(job.started_at) },
            { label: "Finished", value: formatDateTime(job.finished_at) },
          ]}
        />
        <div>
          <p className="text-[13px] text-gray">Scenes</p>
          {counts ? (
            <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[14px] text-ink tabular">
              {total != null ? <li className="font-semibold">{total} planned</li> : null}
              {Object.entries(counts).map(([k, v]) => (
                <li key={k}>
                  {v} {statusMeta("scene", k).label.toLowerCase()}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-0.5 text-[14px] text-gray">Scene production has not started.</p>
          )}
        </div>
        <div className="space-y-3">
          <div>
            <p className="mb-1 flex justify-between text-[13px] text-gray tabular">
              <span>Campaign generations</span>
              <span>
                {b.campaign_used ?? 0} of {b.campaign_limit ?? "—"}
              </span>
            </p>
            <ProgressBar value={b.campaign_limit ? (b.campaign_used ?? 0) / b.campaign_limit : 0} label="Campaign generation budget used" />
          </div>
          <div>
            <p className="mb-1 flex justify-between text-[13px] text-gray tabular">
              <span>Account generations, {b.period}</span>
              <span>
                {b.monthly_used} of {b.monthly_limit}
              </span>
            </p>
            <ProgressBar value={b.monthly_limit ? b.monthly_used / b.monthly_limit : 0} label="Monthly generation budget used" />
          </div>
        </div>
        <div>
          <p className="text-[13px] text-gray">Google Vids project</p>
          {project ? (
            <div className="mt-0.5 text-[14px] text-ink">
              <p className="break-words">{project.title ?? project.project_id}</p>
              <p className="text-[13px] text-gray">
                {projectUrl ? (
                  <a href={projectUrl} target="_blank" rel="noreferrer" className="font-medium text-red-dark underline-offset-2 hover:underline">
                    Open in Google Vids
                  </a>
                ) : (
                  project.url
                )}
                {project.simulated ? " (simulated project)" : ""}
              </p>
            </div>
          ) : (
            <p className="mt-0.5 text-[14px] text-gray">No project yet.</p>
          )}
        </div>
      </div>
    </Section>
  );
}

export function Monitor() {
  const { job, refresh, jobId } = useJobContext();
  const { mutate } = useSWR<JobDetail>(`jobs/${jobId}`);
  if (!job) return null;
  const pending = job.approvals.filter((a) => a.status === "PENDING");
  const needsAction = (job.status === "FAILED" || job.status === "WAITING_FOR_HUMAN") && job.error;

  return (
    <div className="space-y-8">
      {needsAction ? (
        <Notice tone="error" title={job.status === "FAILED" ? "The production failed" : "The production needs a person"}>
          {job.error ? friendlyError(job.error) : "See the stage details below."}
          {job.error?.code ? <span className="ml-1 text-[12px] text-gray">({job.error.code})</span> : null}
        </Notice>
      ) : null}

      {pending.length ? (
        <section aria-labelledby="pending-heading" className="space-y-3">
          <h2 id="pending-heading" className="text-[18px] font-semibold text-ink">
            Waiting for your decision
          </h2>
          {pending.map((a) => (
            <ApprovalCard key={a.id} approval={a} onDecided={() => void refresh()} />
          ))}
          {pending.some((a) => a.kind === "PLAN") ? (
            <p className="text-[13px] text-gray">
              Read the{" "}
              <Link href={`/jobs/${jobId}/plan`} className="font-medium text-red-dark underline-offset-2 hover:underline">
                creative plan
              </Link>{" "}
              and{" "}
              <Link href={`/jobs/${jobId}/storyboard`} className="font-medium text-red-dark underline-offset-2 hover:underline">
                storyboard
              </Link>{" "}
              before approving.
            </p>
          ) : null}
        </section>
      ) : null}

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_380px]">
        <section aria-labelledby="rundown-heading" className="min-w-0">
          <div className="mb-3 flex items-end justify-between gap-3">
            <h2 id="rundown-heading" className="text-[18px] font-semibold text-ink">
              Stages
            </h2>
            <p className="text-[13px] text-gray">Select a stage to see its validation results.</p>
          </div>
          <StageRundown job={job} />
        </section>
        <div className="min-w-0 space-y-6">
          <Controls job={job} onUpdated={(j) => void mutate(j, { revalidate: true })} />
          <NowPanel job={job} />
          <ActivityFeed jobId={jobId} />
        </div>
      </div>
    </div>
  );
}
