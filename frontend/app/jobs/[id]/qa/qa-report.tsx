"use client";

import useSWR from "swr";
import { ArtifactView } from "@/components/artifact";
import { useJobContext } from "@/components/job/job-context";
import { PendingApprovals } from "@/components/job/plan-approval";
import { EmptyState, ErrorNotice, Loading } from "@/components/ui/notice";
import { KeyValues, Section } from "@/components/ui/section";
import { StatusBadge } from "@/components/ui/status-badge";
import { ValidationList, ValidationSummary } from "@/components/validation-list";
import { ApiError } from "@/lib/api";
import { formatBytes, formatDateTime, formatSeconds } from "@/lib/format";
import { CATEGORY_LABELS } from "@/lib/status";
import type { QaDocument, Validation } from "@/lib/types";

interface Issue {
  rule_id: string;
  severity: string;
  message: string;
  subject?: string | null;
  judge?: string;
  suggested_action?: string;
}

interface Probe {
  duration_seconds?: number;
  width?: number;
  height?: number;
  fps?: number;
  video_codec?: string;
  audio_codec?: string;
  audio_rate?: number;
  size_bytes?: number;
  container_ok?: boolean;
  analysis?: {
    decode_ok?: boolean;
    decode_errors?: unknown[];
    integrated_lufs?: number;
    mean_volume_db?: number;
    max_volume_db?: number;
    black_intervals?: unknown[];
    freeze_intervals?: unknown[];
    silence_intervals?: unknown[];
  };
}

export function ProbeFacts({ probe }: { probe: Probe }) {
  const a = probe.analysis;
  const items = [
    { label: "Duration", value: formatSeconds(probe.duration_seconds, 2) },
    { label: "Resolution", value: probe.width && probe.height ? `${probe.width} × ${probe.height}` : "—" },
    { label: "Frame rate", value: probe.fps ? `${probe.fps} fps` : "—" },
    { label: "Codecs", value: [probe.video_codec, probe.audio_codec].filter(Boolean).join(" and ") || "—" },
    { label: "Size", value: formatBytes(probe.size_bytes) },
    { label: "Container", value: probe.container_ok == null ? "—" : probe.container_ok ? "Valid" : "Invalid" },
  ];
  if (a) {
    items.push(
      { label: "Decodes cleanly", value: a.decode_ok == null ? "—" : a.decode_ok ? "Yes" : `No (${a.decode_errors?.length ?? 0} errors)` },
      { label: "Loudness", value: a.integrated_lufs != null ? `${a.integrated_lufs} LUFS` : "—" },
      { label: "Volume", value: a.mean_volume_db != null ? `mean ${a.mean_volume_db} dB, peak ${a.max_volume_db} dB` : "—" },
      {
        label: "Black, frozen, silent",
        value: `${a.black_intervals?.length ?? 0} black, ${a.freeze_intervals?.length ?? 0} frozen, ${a.silence_intervals?.length ?? 0} silent intervals`,
      },
    );
  }
  return <KeyValues items={items} columns={4} />;
}

export function QaReport() {
  const { jobId } = useJobContext();
  const { data, error, mutate } = useSWR<QaDocument>(`jobs/${jobId}/qa`);

  const notYet = error instanceof ApiError && error.status === 404;
  return (
    <div className="space-y-8">
      <PendingApprovals kinds={["COMPLIANCE", "SUBMISSION"]} />
      {notYet ? (
        <EmptyState title="Final QA has not run yet">The report is written after the export is verified. It appears here as soon as the final QA stage has run.</EmptyState>
      ) : error ? (
        <ErrorNotice error={error} onRetry={() => mutate()} />
      ) : !data ? (
        <Loading label="Loading the validation report" lines={5} />
      ) : (
        <Report doc={data} />
      )}
    </div>
  );
}

function Report({ doc }: { doc: QaDocument }) {
  const c = doc.content;
  const sections = (c.sections ?? {}) as Record<string, Validation[]>;
  const issues = (c.actionable_issues ?? []) as unknown as Issue[];
  const all = Object.values(sections).flat();
  const order = ["OPERATIONAL", "CONTENT", "BRAND_POLICY", "TECHNICAL"];
  const keys = [...order.filter((k) => k in sections), ...Object.keys(sections).filter((k) => !order.includes(k))];

  return (
    <>
      <section aria-labelledby="verdict-heading" className="rounded-xl border border-gray-200 p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 id="verdict-heading" className="text-[13px] text-gray">
              Final QA verdict, report version {doc.version}, {formatDateTime(doc.created_at)}
            </h2>
            <p className="mt-2 text-[26px] leading-tight font-bold text-ink">{c.summary ?? "No summary"}</p>
            {c.simulated ? <p className="mt-1 text-[14px] text-red-deep">This report is for a simulated export.</p> : null}
          </div>
          <StatusBadge kind="verdict" value={c.verdict ?? null} />
        </div>
        <div className="mt-4">
          <ValidationSummary list={all} />
        </div>
      </section>

      <Section title="Issues to act on" description="Every failed check, with what to do about it.">
        {issues.length ? (
          <ul className="divide-y divide-gray-100 rounded-lg border border-gray-200">
            {issues.map((i, n) => (
              <li key={`${i.rule_id}-${n}`} className="grid gap-2 px-4 py-3 sm:grid-cols-[100px_minmax(0,1fr)]">
                <StatusBadge kind="severity" value={i.severity} size="sm" />
                <div>
                  <p className="text-[14px] text-ink">{i.message}</p>
                  <p className="text-[12.5px] text-gray">
                    <code>{i.rule_id}</code>
                    {i.subject ? `, ${i.subject}` : ""}
                    {i.judge ? `, judged by ${i.judge}` : ""}
                  </p>
                  {i.suggested_action ? (
                    <p className="mt-1 text-[14px] text-ink">
                      <span className="font-medium">Suggested action: </span>
                      {i.suggested_action}
                    </p>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[14px] text-gray">No failed checks.</p>
        )}
      </Section>

      <div className="space-y-6">
        {keys.map((k) => (
          <Section key={k} title={CATEGORY_LABELS[k] ?? k} actions={<ValidationSummary list={sections[k]} />}>
            <ValidationList list={sections[k]} emptyText="No checks in this category." />
          </Section>
        ))}
      </div>

      {c.export ? (
        <Section title="Export analysis" description="Measured from the exported file.">
          <ProbeFacts probe={c.export as Probe} />
        </Section>
      ) : null}

      {c.frame_artifact_ids?.length ? (
        <Section title="Frames reviewed" description="Sampled from the export for visual QA.">
          <ul className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            {c.frame_artifact_ids.map((id, i) => (
              <li key={id}>
                <ArtifactView id={id} alt={`Export frame ${i + 1}`} />
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </>
  );
}
