"use client";

import Link from "next/link";
import { Lock } from "lucide-react";
import useSWR from "swr";
import { useJobContext } from "@/components/job/job-context";
import { EmptyState, ErrorNotice, Loading } from "@/components/ui/notice";
import { StatusBadge } from "@/components/ui/status-badge";
import { formatDateTime, formatSeconds } from "@/lib/format";
import { useSystem } from "@/lib/hooks";
import { humanise, statusMeta } from "@/lib/status";
import type { Scene } from "@/lib/types";

export function SceneManager() {
  const { jobId } = useJobContext();
  const { data, error, mutate } = useSWR<Scene[]>(`jobs/${jobId}/scenes`);
  const { data: sys } = useSystem();
  const production = (sys?.settings?.production ?? {}) as { max_takes_per_scene?: number; max_edits_per_scene?: number };

  if (error) return <ErrorNotice error={error} onRetry={() => mutate()} />;
  if (!data) return <Loading label="Loading scenes" lines={5} />;
  if (!data.length)
    return <EmptyState title="No scenes yet">Scenes are created when the scene plan is written. Each one is generated, inspected and locked on its own.</EmptyState>;

  const counts = data.reduce<Record<string, number>>((acc, s) => ((acc[s.status] = (acc[s.status] ?? 0) + 1), acc), {});

  return (
    <div className="space-y-6">
      <p className="text-[14px] text-gray tabular">
        {data.length} scenes:{" "}
        {Object.entries(counts)
          .map(([k, v]) => `${v} ${statusMeta("scene", k).label.toLowerCase()}`)
          .join(", ")}
        .
      </p>
      <ol className="space-y-4">
        {data.map((s) => {
          const spec = (s.current_version?.spec ?? {}) as { duration_seconds?: number; narrative_purpose?: string };
          const maxTakes = production.max_takes_per_scene != null ? production.max_takes_per_scene + s.extra_takes_granted : null;
          return (
            <li key={s.id} className={`rounded-xl border bg-white ${s.status === "HUMAN_REVIEW" ? "border-red-200" : "border-gray-200"}`} data-scene={s.scene_key}>
              <div className="flex flex-wrap items-start gap-x-6 gap-y-3 px-5 py-4">
                <div className="w-16 shrink-0">
                  <p className="text-[22px] leading-none font-bold text-ink tabular">{s.scene_key}</p>
                  <p className="mt-1 text-[12.5px] text-gray tabular">{formatSeconds(spec.duration_seconds)}</p>
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-[15px] text-ink">{spec.narrative_purpose ?? "—"}</p>
                  <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-[13px] tabular">
                    <div className="flex gap-1.5">
                      <dt className="text-gray">Takes</dt>
                      <dd className="text-ink">
                        {s.takes_used}
                        {maxTakes != null ? ` of ${maxTakes}` : ""}
                      </dd>
                    </div>
                    <div className="flex gap-1.5">
                      <dt className="text-gray">Edits</dt>
                      <dd className="text-ink">
                        {s.edits_used}
                        {production.max_edits_per_scene != null ? ` of ${production.max_edits_per_scene}` : ""}
                      </dd>
                    </div>
                    <div className="flex gap-1.5">
                      <dt className="text-gray">Version</dt>
                      <dd className="text-ink">{s.current_version?.version ?? "—"}</dd>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <dt className="text-gray">Locked take</dt>
                      <dd className="flex items-center gap-1 text-ink">
                        {s.locked_take_id ? (
                          <>
                            <Lock aria-hidden className="size-3.5 text-gray" />
                            take {s.takes.find((t) => t.id === s.locked_take_id)?.take_no ?? "?"}
                          </>
                        ) : (
                          "none"
                        )}
                      </dd>
                    </div>
                  </dl>
                  {s.pending_instruction ? <p className="mt-2 text-[13px] text-gray-700">Pending instruction: {s.pending_instruction}</p> : null}
                </div>
                <div className="flex items-center gap-3">
                  <StatusBadge kind="scene" value={s.status} />
                  <Link
                    href={`/jobs/${jobId}/scenes/${s.scene_key}`}
                    className={`inline-flex h-8 items-center rounded-md px-3 text-[13px] font-medium ${s.status === "HUMAN_REVIEW" ? "bg-red text-white hover:bg-red-dark" : "border border-gray-300 text-ink hover:border-red hover:text-red-dark"}`}
                  >
                    {s.status === "HUMAN_REVIEW" ? "Review" : "Open"}
                    <span className="visually-hidden"> scene {s.scene_key}</span>
                  </Link>
                </div>
              </div>
              <details className="border-t border-gray-100">
                <summary className="px-5 py-2.5 text-[13px] font-medium text-red-dark">
                  History: {s.versions.length} version{s.versions.length === 1 ? "" : "s"}, {s.takes.length} take{s.takes.length === 1 ? "" : "s"}
                </summary>
                <div className="grid gap-6 px-5 pt-1 pb-5 lg:grid-cols-[280px_minmax(0,1fr)]">
                  <div>
                    <h3 className="mb-2 text-[13px] font-semibold text-ink">Versions</h3>
                    <ol className="space-y-1.5 text-[13px]">
                      {s.versions.map((v) => (
                        <li key={v.id} className="flex gap-2">
                          <span className="w-6 shrink-0 font-semibold text-ink tabular">v{v.version}</span>
                          <span className="min-w-0">
                            {v.reason}
                            <span className="block text-[12px] text-gray">{formatDateTime(v.created_at)}</span>
                          </span>
                        </li>
                      ))}
                    </ol>
                  </div>
                  <div className="overflow-x-auto">
                    <h3 className="mb-2 text-[13px] font-semibold text-ink">Takes</h3>
                    {s.takes.length ? (
                      <table className="w-full min-w-[520px] text-left text-[13px]">
                        <caption className="visually-hidden">Takes for scene {s.scene_key}</caption>
                        <thead className="text-gray">
                          <tr className="border-b border-gray-200">
                            <th scope="col" className="py-1.5 pr-3 font-medium">Take</th>
                            <th scope="col" className="py-1.5 pr-3 font-medium">Origin</th>
                            <th scope="col" className="py-1.5 pr-3 font-medium">Verdict</th>
                            <th scope="col" className="py-1.5 pr-3 font-medium">Decision</th>
                            <th scope="col" className="py-1.5 pr-3 font-medium">Status</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                          {s.takes.map((t) => (
                            <tr key={t.id}>
                              <td className="py-2 pr-3 font-semibold tabular">
                                {t.take_no}
                                {t.locked ? <Lock aria-label="locked" className="ml-1 inline size-3 text-gray" /> : null}
                              </td>
                              <td className="py-2 pr-3">{humanise(t.origin)}</td>
                              <td className="py-2 pr-3">{t.verdict ? <StatusBadge kind="verdict" value={t.verdict} size="sm" /> : "—"}</td>
                              <td className="py-2 pr-3">{t.correction ? humanise(t.correction) : "—"}</td>
                              <td className="py-2 pr-3">{humanise(t.status)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : (
                      <p className="text-[13px] text-gray">No takes yet.</p>
                    )}
                  </div>
                </div>
              </details>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
