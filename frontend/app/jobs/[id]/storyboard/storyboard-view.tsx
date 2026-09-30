"use client";

import useSWR from "swr";
import { useJobContext } from "@/components/job/job-context";
import { PendingApprovals } from "@/components/job/plan-approval";
import { EmptyState, ErrorNotice, Loading } from "@/components/ui/notice";
import { Section } from "@/components/ui/section";
import { formatSeconds, timecode } from "@/lib/format";
import { humanise } from "@/lib/status";
import type { Plan } from "@/lib/types";

interface Line {
  id: string;
  beat_id: string;
  kind: string;
  speaker: string;
  text: string;
  fact_ids?: string[];
}
interface Super {
  id: string;
  beat_id: string;
  text: string;
  purpose: string;
}
interface ScriptDoc {
  lines?: Line[];
  supers?: Super[];
  cta_line_id?: string;
  disclosure_texts?: string[];
  estimated_speech_seconds?: number;
}
interface SceneEntry {
  scene_key: string;
  order: number;
  beat_id: string;
  narrative_purpose: string;
  duration_seconds: number;
  visual_description: string;
  camera_direction: string;
  lighting: string;
  character_ids?: string[];
  location_id: string;
  line_ids?: string[];
  super_ids?: string[];
  transition_out?: string;
  depends_on?: string[];
  validation_criteria?: string[];
}
interface ScenePlanDoc {
  total_duration_seconds?: number;
  scenes?: SceneEntry[];
  rationale?: string;
}
interface Prompt {
  scene_key: string;
  prompt: string;
  duration_seconds: number;
  aspect_ratio: string;
  continuity_tokens?: string[];
  spoken_lines?: string[];
  reference_asset_ids?: string[];
}
interface CreativeDoc {
  beats?: { id: string; name: string }[];
  characters?: { id: string; name: string }[];
  locations?: { id: string; name: string }[];
}

function startTimes(scenes: SceneEntry[]): number[] {
  const out: number[] = [];
  scenes.reduce((acc, s) => {
    out.push(acc);
    return acc + s.duration_seconds;
  }, 0);
  return out;
}

/** Scene durations drawn to scale: the film's running order at a glance. */
function DurationRuler({ scenes, total }: { scenes: SceneEntry[]; total: number }) {
  const sum = scenes.reduce((a, s) => a + s.duration_seconds, 0) || 1;
  const starts = startTimes(scenes);
  return (
    <figure>
      <figcaption className="visually-hidden">
        Scene durations: {scenes.map((s) => `${s.scene_key} ${s.duration_seconds} seconds`).join(", ")}
      </figcaption>
      <div aria-hidden className="flex h-14 w-full overflow-hidden rounded-lg border border-gray-300">
        {scenes.map((s, i) => (
          <a
            key={s.scene_key}
            href={`#scene-${s.scene_key}`}
            tabIndex={-1}
            style={{ width: `${(s.duration_seconds / sum) * 100}%` }}
            className={`flex min-w-0 flex-col justify-between px-2 py-1.5 text-[12px] hover:bg-red-50 ${i ? "border-l border-gray-300" : ""} ${i % 2 ? "bg-gray-50" : "bg-white"}`}
          >
            <span className="truncate font-semibold text-ink">{s.scene_key}</span>
            <span className="truncate text-gray tabular">{s.duration_seconds}s</span>
          </a>
        ))}
      </div>
      <div aria-hidden className="relative mt-1 h-4 text-[11px] text-gray tabular">
        {scenes.map((s, i) => (
          <span key={s.scene_key} className="absolute" style={{ left: `${(starts[i] / sum) * 100}%` }}>
            {timecode(starts[i])}
          </span>
        ))}
        <span className="absolute right-0">{timecode(total)}</span>
      </div>
    </figure>
  );
}

export function Storyboard() {
  const { jobId } = useJobContext();
  const { data, error, mutate } = useSWR<Plan>(`jobs/${jobId}/plan`);
  if (error) return <ErrorNotice error={error} onRetry={() => mutate()} />;
  if (!data) return <Loading label="Loading the script and storyboard" lines={5} />;

  const script = data.SCRIPT?.content as ScriptDoc | undefined;
  const scenePlan = data.SCENE_PLAN?.content as ScenePlanDoc | undefined;
  const prompts = ((data.PROMPTS?.content as { prompts?: Prompt[] } | undefined)?.prompts ?? []) as Prompt[];
  const creative = data.CREATIVE_PLAN?.content as CreativeDoc | undefined;

  const lines = new Map((script?.lines ?? []).map((l) => [l.id, l]));
  const supers = new Map((script?.supers ?? []).map((s) => [s.id, s]));
  const beats = new Map((creative?.beats ?? []).map((b) => [b.id, b.name]));
  const chars = new Map((creative?.characters ?? []).map((c) => [c.id, c.name]));
  const locs = new Map((creative?.locations ?? []).map((l) => [l.id, l.name]));
  const promptByScene = new Map(prompts.map((p) => [p.scene_key, p]));
  const scenes = [...(scenePlan?.scenes ?? [])].sort((a, b) => a.order - b.order);
  const total = scenePlan?.total_duration_seconds ?? scenes.reduce((a, s) => a + s.duration_seconds, 0);

  const starts = startTimes(scenes);

  return (
    <div className="space-y-10">
      <PendingApprovals kinds={["PLAN"]} />

      {scenes.length ? (
        <section aria-labelledby="film-heading">
          <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
            <div>
              <h2 id="film-heading" className="text-[18px] font-semibold text-ink">
                The film
              </h2>
              {scenePlan?.rationale ? <p className="mt-0.5 max-w-3xl text-[14px] text-gray">{scenePlan.rationale}</p> : null}
            </div>
            <dl className="flex gap-8">
              <div>
                <dt className="text-[13px] text-gray">Running time</dt>
                <dd className="text-[28px] leading-none font-bold text-ink tabular">{timecode(total)}</dd>
              </div>
              <div>
                <dt className="text-[13px] text-gray">Scenes</dt>
                <dd className="text-[28px] leading-none font-bold text-ink tabular">{scenes.length}</dd>
              </div>
              <div>
                <dt className="text-[13px] text-gray">Estimated speech</dt>
                <dd className="text-[28px] leading-none font-bold text-ink tabular">{script?.estimated_speech_seconds != null ? `${script.estimated_speech_seconds.toFixed(1)}s` : "—"}</dd>
              </div>
            </dl>
          </div>
          <DurationRuler scenes={scenes} total={total} />
        </section>
      ) : null}

      {script ? (
        <Section title="Script" description={`Version ${data.SCRIPT?.version}. Lines that carry a claim cite the approved fact they rely on.`}>
          <div className="-mx-5 -my-4 overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-[14px]">
              <caption className="visually-hidden">Script lines</caption>
              <thead className="bg-gray-50 text-[13px] text-gray">
                <tr>
                  <th scope="col" className="px-5 py-2.5 font-medium">Beat</th>
                  <th scope="col" className="px-5 py-2.5 font-medium">Speaker</th>
                  <th scope="col" className="px-5 py-2.5 font-medium">Line</th>
                  <th scope="col" className="px-5 py-2.5 font-medium">Facts</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(script.lines ?? []).map((l) => (
                  <tr key={l.id} className="align-top">
                    <td className="px-5 py-3 text-gray">{beats.get(l.beat_id) ?? l.beat_id}</td>
                    <td className="px-5 py-3">
                      <span className="font-medium text-ink">{l.speaker}</span>
                      <span className="block text-[12.5px] text-gray">{humanise(l.kind)}</span>
                    </td>
                    <td className="px-5 py-3 text-[15px] text-ink">
                      {l.text}
                      {l.id === script.cta_line_id ? <span className="mt-1 block text-[12px] font-medium text-red-dark">Call to action</span> : null}
                    </td>
                    <td className="px-5 py-3 text-[13px] text-gray">{l.fact_ids?.length ? l.fact_ids.join(", ") : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-8 grid gap-6 md:grid-cols-2">
            <div>
              <h3 className="mb-2 text-[14px] font-semibold">On-screen text</h3>
              {script.supers?.length ? (
                <ul className="space-y-2 text-[14px]">
                  {script.supers.map((s) => (
                    <li key={s.id} className="rounded-md border border-gray-200 px-3 py-2">
                      {s.text}
                      <span className="block text-[12.5px] text-gray">{s.purpose}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[14px] text-gray">None</p>
              )}
            </div>
            <div>
              <h3 className="mb-2 text-[14px] font-semibold">Disclosures, verbatim</h3>
              {script.disclosure_texts?.length ? (
                <ul className="space-y-2 text-[13px]">
                  {script.disclosure_texts.map((d, i) => (
                    <li key={i} className="rounded-md border border-gray-200 px-3 py-2">
                      {d}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[14px] text-gray">None</p>
              )}
            </div>
          </div>
        </Section>
      ) : (
        <EmptyState title="No script yet">The script stage writes it after the creative plan.</EmptyState>
      )}

      {scenes.length ? (
        <section aria-labelledby="board-heading">
          <h2 id="board-heading" className="mb-4 text-[18px] font-semibold text-ink">
            Storyboard
          </h2>
          <ol className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
            {scenes.map((s, i) => {
              const start = starts[i];
              const end = start + s.duration_seconds;
              const prompt = promptByScene.get(s.scene_key);
              return (
                <li key={s.scene_key} id={`scene-${s.scene_key}`} className="flex scroll-mt-24 flex-col rounded-xl border border-gray-200 bg-white">
                  <div className="flex items-baseline justify-between gap-3 border-b border-gray-100 px-4 py-3">
                    <h3 className="text-[20px] font-bold text-ink tabular">{s.scene_key}</h3>
                    <p className="text-[13px] text-gray tabular">
                      {timecode(start)} to {timecode(end)}, {formatSeconds(s.duration_seconds)}
                    </p>
                  </div>
                  <div className="flex-1 space-y-3 px-4 py-4 text-[14px]">
                    <p className="text-[13px] font-medium text-red-dark">{beats.get(s.beat_id) ?? s.beat_id}</p>
                    <p className="text-[15px] leading-relaxed text-ink">{s.visual_description}</p>
                    <dl className="grid grid-cols-[88px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13px]">
                      <dt className="text-gray">Purpose</dt>
                      <dd>{s.narrative_purpose}</dd>
                      <dt className="text-gray">Camera</dt>
                      <dd>{s.camera_direction}</dd>
                      <dt className="text-gray">Lighting</dt>
                      <dd>{s.lighting}</dd>
                      <dt className="text-gray">Location</dt>
                      <dd>{locs.get(s.location_id) ?? s.location_id}</dd>
                      <dt className="text-gray">Cast</dt>
                      <dd>{s.character_ids?.length ? s.character_ids.map((c) => chars.get(c) ?? c).join(", ") : "No one on screen"}</dd>
                      <dt className="text-gray">Transition</dt>
                      <dd>{humanise(s.transition_out ?? "CUT")} out</dd>
                      {s.depends_on?.length ? (
                        <>
                          <dt className="text-gray">Continuity</dt>
                          <dd>Matches {s.depends_on.join(", ")}</dd>
                        </>
                      ) : null}
                    </dl>
                    {s.line_ids?.length ? (
                      <div>
                        <p className="text-[13px] text-gray">Lines</p>
                        <ul className="mt-1 space-y-1">
                          {s.line_ids.map((id) => {
                            const l = lines.get(id);
                            return (
                              <li key={id} className="border-l-2 border-gray-300 pl-2.5 text-[14px] italic text-ink">
                                {l ? `${l.speaker}: “${l.text}”` : id}
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    ) : null}
                    {s.super_ids?.length ? (
                      <div>
                        <p className="text-[13px] text-gray">On-screen text</p>
                        <ul className="mt-1 space-y-1">
                          {s.super_ids.map((id) => (
                            <li key={id} className="text-[14px] font-medium text-ink">
                              {supers.get(id)?.text ?? id}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {s.validation_criteria?.length ? (
                      <div>
                        <p className="text-[13px] text-gray">Inspection checks</p>
                        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-[13px] marker:text-gray-400">
                          {s.validation_criteria.map((c, i) => (
                            <li key={i}>{c}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </div>
                  {prompt ? (
                    <details className="border-t border-gray-100 px-4 py-3">
                      <summary className="text-[13px] font-medium text-red-dark">Generation prompt</summary>
                      <pre className="mt-2 max-h-72 overflow-auto rounded-md bg-gray-50 p-3 font-sans text-[12.5px] leading-relaxed whitespace-pre-wrap text-ink">{prompt.prompt}</pre>
                      <p className="mt-2 text-[12.5px] text-gray tabular">
                        {prompt.duration_seconds}s at {prompt.aspect_ratio}
                        {prompt.reference_asset_ids?.length ? `, ${prompt.reference_asset_ids.length} reference image(s)` : ""}
                      </p>
                      {prompt.continuity_tokens?.length ? (
                        <p className="mt-1 text-[12.5px] text-gray">Must appear verbatim: {prompt.continuity_tokens.map((t) => `“${t}”`).join(", ")}</p>
                      ) : null}
                    </details>
                  ) : (
                    <p className="border-t border-gray-100 px-4 py-3 text-[13px] text-gray">No prompt written yet.</p>
                  )}
                </li>
              );
            })}
          </ol>
        </section>
      ) : (
        <EmptyState title="No scene plan yet">The scene planner sizes scenes to the script. They appear here when it has run.</EmptyState>
      )}
    </div>
  );
}
