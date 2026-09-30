"use client";

import { ChevronDown, Download } from "lucide-react";
import useSWR from "swr";
import { ArtifactView } from "@/components/artifact";
import { useJobContext } from "@/components/job/job-context";
import { PendingApprovals } from "@/components/job/plan-approval";
import { buttonClass } from "@/components/ui/button";
import { EmptyState, ErrorNotice, Loading, Notice } from "@/components/ui/notice";
import { KeyValues, Section } from "@/components/ui/section";
import { StatusBadge } from "@/components/ui/status-badge";
import { ValidationList, ValidationSummary } from "@/components/validation-list";
import { artifactContentUrl } from "@/lib/api";
import { formatBytes, formatDateTime, formatSeconds } from "@/lib/format";
import { humanise } from "@/lib/status";
import type { DeliveryOut, ExportOut } from "@/lib/types";
import { ProbeFacts } from "../qa/qa-report";

export function Delivery() {
  const { jobId } = useJobContext();
  const { data, error, mutate } = useSWR<{ exports: ExportOut[]; deliveries: DeliveryOut[] }>(`jobs/${jobId}/exports`);

  if (error) return <ErrorNotice error={error} onRetry={() => mutate()} />;
  if (!data) return <Loading label="Loading exports" lines={4} />;

  const verified = [...data.exports].reverse().find((e) => e.status === "VERIFIED" && e.artifact_id);
  const byExport = new Map(data.exports.map((e) => [e.id, e]));

  return (
    <div className="space-y-8">
      <PendingApprovals kinds={["COMPLIANCE", "SUBMISSION"]} />

      {verified ? (
        <section aria-labelledby="final-heading" className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="min-w-0">
            <h2 id="final-heading" className="visually-hidden">
              Verified export
            </h2>
            <ArtifactView id={verified.artifact_id!} alt={`Verified export, attempt ${verified.attempt}${verified.simulated ? " (simulated)" : ""}`} />
          </div>
          <div className="space-y-4">
            <StatusBadge kind="export" value={verified.status} />
            <KeyValues
              columns={1}
              items={[
                { label: "Export attempt", value: verified.attempt },
                { label: "Format", value: verified.format.toUpperCase() },
                { label: "Duration", value: formatSeconds(verified.duration_seconds, 2) },
                { label: "Size", value: formatBytes(verified.size_bytes) },
                { label: "Exported", value: formatDateTime(verified.created_at) },
              ]}
            />
            {verified.simulated ? <Notice tone="attention">Simulated output from the mock adapter. It was not produced by Google Vids.</Notice> : null}
            <a href={artifactContentUrl(verified.artifact_id!)} download className={buttonClass("primary", "md")}>
              <Download aria-hidden className="size-4" /> Download the video
            </a>
          </div>
        </section>
      ) : (
        <EmptyState title="No verified export yet">
          {data.exports.length ? "An export was attempted but has not passed verification. See the attempts below." : "The export stage runs after the timeline is assembled and previewed."}
        </EmptyState>
      )}

      <details className="group rounded-xl border border-gray-200 bg-white">
        <summary className="cursor-pointer list-none rounded-xl px-5 py-4 text-[15px] font-semibold text-ink hover:bg-gray-50">
          <span className="inline-flex items-center gap-2">
            <ChevronDown aria-hidden className="size-4 transition-transform group-open:rotate-180" />
            Technical details
          </span>
          <span className="mt-0.5 block pl-6 text-[13px] font-normal text-gray">Where the video was stored, and the checks each export passed.</span>
        </summary>
        <div className="space-y-6 border-t border-gray-100 p-5">
      <Section title="Deliveries" description="Where the approved video was stored or submitted.">
        {data.deliveries.length ? (
          <ul className="divide-y divide-gray-100 rounded-lg border border-gray-200">
            {data.deliveries.map((d) => {
              const exp = byExport.get(d.export_id);
              return (
                <li key={d.id} className="grid gap-3 px-4 py-4 md:grid-cols-[140px_minmax(0,1fr)]">
                  <div>
                    <StatusBadge kind="export" value={d.status} size="sm" />
                  </div>
                  <div className="min-w-0 space-y-1 text-[14px]">
                    <p className="text-ink">
                      {humanise(d.target)}
                      {d.simulated ? <span className="text-gray"> (simulated)</span> : null}
                      <span className="text-gray">, {formatDateTime(d.created_at)}</span>
                    </p>
                    {d.location ? <p className="text-[13px] break-all text-gray">Location: {d.location}</p> : null}
                    <p className="text-[13px] break-all text-gray">
                      SHA-256 <code className="text-ink">{d.sha256}</code>
                    </p>
                    {d.note ? <p className="text-[13px] text-gray-700">Note: {d.note}</p> : null}
                    {exp?.artifact_id ? (
                      <a href={artifactContentUrl(exp.artifact_id)} download className="inline-block text-[13px] font-medium text-red-dark underline-offset-2 hover:underline">
                        Download the delivered export (attempt {exp.attempt})
                      </a>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-[14px] text-gray">Nothing has been delivered. Delivery happens after compliance approval.</p>
        )}
      </Section>

      <Section title="Export attempts" description="Each export is verified against the plan before it can be approved.">
        {data.exports.length ? (
          <ol className="space-y-6">
            {[...data.exports].reverse().map((e) => (
              <li key={e.id} className="rounded-lg border border-gray-200 p-4">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                  <h3 className="text-[15px] font-semibold text-ink">
                    Attempt {e.attempt}
                    <span className="ml-2 text-[13px] font-normal text-gray">
                      {formatDateTime(e.created_at)}, {formatSeconds(e.duration_seconds, 2)}, {formatBytes(e.size_bytes)}
                      {e.simulated ? ", simulated" : ""}
                    </span>
                  </h3>
                  <StatusBadge kind="export" value={e.status} size="sm" />
                </div>
                {e.probe ? (
                  <div className="mb-4">
                    <ProbeFacts probe={e.probe} />
                  </div>
                ) : null}
                <div className="mb-2">
                  <ValidationSummary list={e.verification} />
                </div>
                <ValidationList list={e.verification} emptyText="No verification recorded." />
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-[14px] text-gray">No export has been attempted.</p>
        )}
      </Section>
        </div>
      </details>
    </div>
  );
}
