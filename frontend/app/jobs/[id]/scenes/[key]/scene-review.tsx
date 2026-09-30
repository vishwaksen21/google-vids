"use client";

import Link from "next/link";
import { Lock } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { ArtifactView } from "@/components/artifact";
import { useJobContext } from "@/components/job/job-context";
import { Button } from "@/components/ui/button";
import { TextArea } from "@/components/ui/field";
import { EmptyState, ErrorNotice, Loading, Notice } from "@/components/ui/notice";
import { Section } from "@/components/ui/section";
import { StatusBadge } from "@/components/ui/status-badge";
import { GroupedValidations, ValidationSummary } from "@/components/validation-list";
import { apiRequest } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import { humanise } from "@/lib/status";
import type { Approval, Scene } from "@/lib/types";

type Action = "accept" | "regenerate" | "edit" | "reject";

const ACTIONS: { value: Action; label: string; help: string }[] = [
  { value: "accept", label: "Accept", help: "Locks the latest take. It will not be regenerated or edited again." },
  { value: "regenerate", label: "Regenerate", help: "Generates this scene again (spends a generation). Other scenes are untouched." },
  { value: "edit", label: "Edit", help: "Asks Google Vids to change the latest take according to your instruction." },
  { value: "reject", label: "Reject", help: "Rejects the scene. The production stops for a person to decide what to do next." },
];

function ReviewPanel({ jobId, sceneKey, approval, onDone }: { jobId: string; sceneKey: string; approval: Approval | undefined; onDone: () => void }) {
  const [action, setAction] = useState<Action>("accept");
  const [reason, setReason] = useState("");
  const [instruction, setInstruction] = useState("");
  const [errors, setErrors] = useState<{ reason?: string; instruction?: string }>({});
  const [result, setResult] = useState<string | null>(null);

  const submit = useAction(async () => {
    const body: Record<string, unknown> = { action, reason: reason.trim() };
    if (instruction.trim() && (action === "edit" || action === "regenerate")) body.instruction = instruction.trim();
    const decided = await apiRequest<Approval>(`jobs/${jobId}/scenes/${sceneKey}/review`, { method: "POST", body });
    setResult(`Recorded: ${decided.title} is ${decided.status.toLowerCase()} by ${decided.decided_by}.`);
    onDone();
    return decided;
  });

  const send = (e: React.FormEvent) => {
    e.preventDefault();
    const next: typeof errors = {};
    if (!reason.trim()) next.reason = "Give a reason. It is recorded with your decision.";
    if (action === "edit" && !instruction.trim()) next.instruction = "Say what should change.";
    setErrors(next);
    if (Object.keys(next).length) return;
    void submit.run();
  };

  return (
    <section aria-labelledby="review-heading" className="rounded-xl border border-red-200 bg-white p-5">
      <h2 id="review-heading" className="text-[18px] font-semibold text-ink">
        This scene needs your review
      </h2>
      <p className="mt-1 max-w-prose text-[14px] text-gray-700">{approval?.summary || "Automatic inspection could not decide on this scene."}</p>
      {result ? (
        <Notice className="mt-4" title={result} />
      ) : (
        <form onSubmit={send} className="mt-4 space-y-4" noValidate>
          <fieldset>
            <legend className="mb-2 text-[14px] font-medium text-ink">Decision</legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {ACTIONS.map((a) => (
                <label
                  key={a.value}
                  className={`flex cursor-pointer gap-3 rounded-lg border px-3 py-2.5 ${action === a.value ? "border-red bg-red-50" : "border-gray-200 hover:border-gray-400"}`}
                >
                  <input type="radio" name="scene-action" value={a.value} checked={action === a.value} onChange={() => setAction(a.value)} className="mt-1" />
                  <span>
                    <span className="block text-[14px] font-semibold text-ink">{a.label}</span>
                    <span className="block text-[13px] text-gray-700">{a.help}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          {action === "edit" || action === "regenerate" ? (
            <TextArea
              label={action === "edit" ? "Edit instruction" : "Instruction for the new take"}
              optional={action === "regenerate"}
              rows={2}
              maxLength={1000}
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              error={errors.instruction}
              hint="Be specific: what is wrong, and what it should look like."
            />
          ) : null}
          <TextArea label="Reason" rows={2} maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)} error={errors.reason} />
          <ErrorNotice error={submit.error} />
          <Button type="submit" variant="primary" loading={submit.pending}>
            {ACTIONS.find((a) => a.value === action)?.label} scene {sceneKey}
          </Button>
        </form>
      )}
    </section>
  );
}

export function SceneReview({ sceneKey }: { sceneKey: string }) {
  const { jobId, job, refresh } = useJobContext();
  const { data: scene, error, mutate } = useSWR<Scene>(`jobs/${jobId}/scenes/${sceneKey}`);

  if (error) return <ErrorNotice error={error} onRetry={() => mutate()} title={`Scene ${sceneKey} could not be loaded`} />;
  if (!scene) return <Loading label={`Loading scene ${sceneKey}`} lines={5} />;

  const approval = job?.approvals.find((a) => a.kind === "SCENE_REVIEW" && a.status === "PENDING" && a.subject.startsWith(`scene:${sceneKey}:`));
  const prompt = (scene.current_version?.prompt ?? {}) as { prompt?: string; duration_seconds?: number; aspect_ratio?: string };
  const takes = [...scene.takes].reverse();

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-[13px]">
            <Link href={`/jobs/${jobId}/scenes`} className="font-medium text-red-dark underline-offset-2 hover:underline">
              All scenes
            </Link>
          </p>
          <h2 className="mt-1 text-[24px] font-bold text-ink tabular">Scene {scene.scene_key}</h2>
          <p className="text-[14px] text-gray tabular">
            Version {scene.current_version?.version ?? "—"} ({scene.current_version?.reason ?? "no version"}), {scene.takes_used} take{scene.takes_used === 1 ? "" : "s"} used, {scene.edits_used} edit
            {scene.edits_used === 1 ? "" : "s"} used
          </p>
        </div>
        <StatusBadge kind="scene" value={scene.status} />
      </div>

      {scene.status === "HUMAN_REVIEW" || approval ? (
        <ReviewPanel
          jobId={jobId}
          sceneKey={scene.scene_key}
          approval={approval}
          onDone={() => {
            void mutate();
            void refresh();
          }}
        />
      ) : null}

      <Section title="Current prompt" description={prompt.duration_seconds ? `${prompt.duration_seconds}s at ${prompt.aspect_ratio}` : undefined}>
        {prompt.prompt ? (
          <pre className="max-h-80 overflow-auto rounded-md bg-gray-50 p-4 font-sans text-[13px] leading-relaxed whitespace-pre-wrap text-ink">{prompt.prompt}</pre>
        ) : (
          <p className="text-[14px] text-gray">No prompt recorded for this version.</p>
        )}
        {scene.pending_instruction ? <p className="mt-3 text-[14px]">Pending instruction: {scene.pending_instruction}</p> : null}
      </Section>

      <section aria-labelledby="takes-heading">
        <h2 id="takes-heading" className="mb-4 text-[18px] font-semibold text-ink">
          Takes, newest first
        </h2>
        {takes.length === 0 ? (
          <EmptyState title="No takes yet">The scene has not been generated.</EmptyState>
        ) : (
          <ol className="space-y-6">
            {takes.map((t) => (
              <li key={t.id} className="rounded-xl border border-gray-200 bg-white" aria-labelledby={`take-${t.id}`}>
                <div className="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 px-5 py-4">
                  <div>
                    <h3 id={`take-${t.id}`} className="flex items-center gap-2 text-[16px] font-semibold text-ink">
                      Take {t.take_no}
                      {t.locked ? (
                        <span className="inline-flex items-center gap-1 text-[12.5px] font-medium text-gray">
                          <Lock aria-hidden className="size-3.5" /> locked
                        </span>
                      ) : null}
                    </h3>
                    <p className="text-[13px] text-gray">
                      {t.origin === "EDIT" ? "Edited from the previous take" : "Generated"}, {formatDateTime(t.created_at)}
                      {t.simulated ? ", simulated" : ""}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    {t.verdict ? <StatusBadge kind="verdict" value={t.verdict} /> : null}
                    <span className="text-[13px] text-gray">{humanise(t.status)}</span>
                  </div>
                </div>
                <div className="space-y-5 px-5 py-4">
                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <p className="text-[13px] text-gray">Correction decision</p>
                      <p className="text-[15px] text-ink">{t.correction ? humanise(t.correction) : "None yet"}</p>
                      {t.edit_instruction ? <p className="mt-1 text-[14px] text-gray-700">Instruction: {t.edit_instruction}</p> : null}
                    </div>
                    <div>
                      <p className="text-[13px] text-gray">Verdict reasons</p>
                      {t.verdict_reasons.length ? (
                        <ul className="list-disc pl-5 text-[14px] text-ink marker:text-gray-400">
                          {t.verdict_reasons.map((r, i) => (
                            <li key={i}>{String(r)}</li>
                          ))}
                        </ul>
                      ) : (
                        <p className="text-[14px] text-ink">None given</p>
                      )}
                    </div>
                  </div>
                  <div>
                    <p className="mb-2 text-[13px] text-gray">Evidence ({t.evidence.length})</p>
                    {t.evidence.length ? (
                      <ul className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-5">
                        {t.evidence.map((id) => (
                          <li key={id}>
                            <ArtifactView id={id} />
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-[14px] text-gray">No evidence captured.</p>
                    )}
                  </div>
                  <div>
                    <div className="mb-2 flex items-baseline justify-between gap-3">
                      <h4 className="text-[15px] font-semibold text-ink">Inspection results</h4>
                      <ValidationSummary list={t.validations} />
                    </div>
                    <GroupedValidations list={t.validations} />
                  </div>
                  <details>
                    <summary className="text-[13px] font-medium text-red-dark">Prompt used for this take</summary>
                    <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-gray-50 p-3 font-sans text-[12.5px] whitespace-pre-wrap">{t.prompt_text}</pre>
                  </details>
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
