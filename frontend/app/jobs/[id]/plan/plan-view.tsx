"use client";

import { Hand } from "lucide-react";
import useSWR from "swr";
import { useJobContext } from "@/components/job/job-context";
import { PendingApprovals } from "@/components/job/plan-approval";
import { EmptyState, ErrorNotice, Loading } from "@/components/ui/notice";
import { KeyValues, Section } from "@/components/ui/section";
import type { Plan } from "@/lib/types";

type Brief = {
  objective?: string;
  audience?: string;
  product?: string;
  key_message?: string;
  cta?: string;
  tone?: string;
  claims?: { text: string; fact_id?: string | null }[];
  disclosures?: { text: string; on_screen?: boolean; spoken?: boolean }[];
  brand_rules?: string[];
  mandatory_elements?: string[];
  open_questions?: string[];
  output?: { aspect_ratio?: string; min_duration_seconds?: number | null; max_duration_seconds?: number | null; language?: string; captions_required?: boolean; voiceover_required?: boolean; end_card_required?: boolean };
};

type Creative = {
  title?: string;
  logline?: string;
  concept?: string;
  emotional_direction?: string;
  visual_direction?: string;
  beats?: { id: string; name: string; purpose: string; emotion: string }[];
  characters?: { id: string; name: string; role: string; appearance: string; wardrobe: string }[];
  locations?: { id: string; name: string; description: string }[];
  objective_beat_id?: string;
  cta_beat_id?: string;
};

function List({ items, empty = "None" }: { items?: string[]; empty?: string }) {
  if (!items?.length) return <p className="text-[14px] text-gray">{empty}</p>;
  return (
    <ul className="list-disc space-y-1 pl-5 text-[14px] text-ink marker:text-gray-400">
      {items.map((t, i) => (
        <li key={i}>{t}</li>
      ))}
    </ul>
  );
}

export function PlanView() {
  const { jobId } = useJobContext();
  const { data, error, mutate } = useSWR<Plan>(`jobs/${jobId}/plan`);

  if (error) return <ErrorNotice error={error} onRetry={() => mutate()} />;
  if (!data) return <Loading label="Loading the plan" lines={5} />;

  const brief = data.CAMPAIGN_BRIEF?.content as Brief | undefined;
  const plan = data.CREATIVE_PLAN?.content as Creative | undefined;

  return (
    <div className="space-y-8">
      <PendingApprovals kinds={["PLAN"]} />

      {brief ? (
        <Section title="Validated brief" description={`What the intake agent understood from the campaign brief (version ${data.CAMPAIGN_BRIEF?.version}).`}>
          <div className="space-y-6">
            {brief.open_questions?.length ? (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3">
                <h3 className="flex items-center gap-2 text-[14px] font-semibold text-red-deep">
                  <Hand aria-hidden className="size-4 text-red" /> {brief.open_questions.length} open question{brief.open_questions.length === 1 ? "" : "s"} for the reviewer
                </h3>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-[14px] text-ink">
                  {brief.open_questions.map((q, i) => (
                    <li key={i}>{q}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="text-[14px] text-gray">The intake agent recorded no open questions.</p>
            )}
            <KeyValues
              columns={3}
              items={[
                { label: "Objective", value: brief.objective ?? "—" },
                { label: "Audience", value: brief.audience ?? "—" },
                { label: "Product", value: brief.product ?? "—" },
                { label: "Key message", value: brief.key_message ?? "—" },
                { label: "Call to action", value: brief.cta ?? "—" },
                { label: "Tone", value: brief.tone ?? "—" },
              ]}
            />
            <div className="grid gap-6 md:grid-cols-2">
              <div>
                <h3 className="mb-2 text-[14px] font-semibold">Claims</h3>
                {brief.claims?.length ? (
                  <ul className="space-y-2 text-[14px]">
                    {brief.claims.map((c, i) => (
                      <li key={i} className="rounded-md border border-gray-200 px-3 py-2">
                        {c.text}
                        <span className="block text-[12.5px] text-gray">{c.fact_id ? `Backed by ${c.fact_id}` : "No approved fact"}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-[14px] text-gray">None</p>
                )}
              </div>
              <div>
                <h3 className="mb-2 text-[14px] font-semibold">Disclosures</h3>
                <List items={brief.disclosures?.map((d) => d.text)} />
              </div>
              <div>
                <h3 className="mb-2 text-[14px] font-semibold">Brand rules</h3>
                <List items={brief.brand_rules} />
              </div>
              <div>
                <h3 className="mb-2 text-[14px] font-semibold">Mandatory elements</h3>
                <List items={brief.mandatory_elements} />
              </div>
            </div>
            {brief.output ? (
              <KeyValues
                columns={4}
                items={[
                  { label: "Aspect ratio", value: brief.output.aspect_ratio ?? "—" },
                  { label: "Duration bounds", value: `${brief.output.min_duration_seconds ?? "any"} to ${brief.output.max_duration_seconds ?? "any"} s` },
                  { label: "Language", value: brief.output.language ?? "—" },
                  {
                    label: "Includes",
                    value:
                      [brief.output.captions_required ? "captions" : null, brief.output.voiceover_required ? "voiceover" : null, brief.output.end_card_required ? "end card" : null]
                        .filter(Boolean)
                        .join(", ") || "none",
                  },
                ]}
              />
            ) : null}
          </div>
        </Section>
      ) : (
        <EmptyState title="The brief has not been validated yet">The intake stage writes it. It appears here as soon as the stage has run.</EmptyState>
      )}

      {plan ? (
        <section aria-labelledby="creative-heading" className="rounded-xl border border-gray-200">
          <div className="border-b border-gray-100 px-6 py-6">
            <h2 id="creative-heading" className="text-[13px] text-gray">
              Creative plan, version {data.CREATIVE_PLAN?.version}
            </h2>
            <p className="mt-2 max-w-3xl text-[26px] leading-tight font-bold tracking-[-0.01em] text-ink">{plan.logline}</p>
            <p className="mt-2 text-[15px] text-gray">{plan.title}</p>
          </div>
          <div className="grid gap-8 px-6 py-6 lg:grid-cols-2">
            <div className="space-y-5">
              <div>
                <h3 className="text-[14px] font-semibold">Concept</h3>
                <p className="mt-1 max-w-prose text-[15px] leading-relaxed">{plan.concept}</p>
              </div>
              <div>
                <h3 className="text-[14px] font-semibold">Emotional direction</h3>
                <p className="mt-1 text-[15px]">{plan.emotional_direction}</p>
              </div>
              <div>
                <h3 className="text-[14px] font-semibold">Visual direction</h3>
                <p className="mt-1 max-w-prose text-[15px] leading-relaxed">{plan.visual_direction}</p>
              </div>
            </div>
            <div>
              <h3 className="mb-3 text-[14px] font-semibold">Beats</h3>
              <ol className="space-y-0">
                {(plan.beats ?? []).map((b, i) => (
                  <li key={b.id} className="grid grid-cols-[32px_minmax(0,1fr)] gap-3 border-l border-gray-200 pb-4 pl-4 last:pb-0">
                    <span className="-ml-[33px] flex size-8 items-center justify-center rounded-full border border-gray-300 bg-white text-[13px] font-semibold text-gray tabular">{i + 1}</span>
                    <div>
                      <p className="text-[15px] font-semibold text-ink">
                        {b.name}
                        {b.id === plan.objective_beat_id ? <span className="ml-2 text-[12px] font-medium text-red-dark">delivers the objective</span> : null}
                        {b.id === plan.cta_beat_id ? <span className="ml-2 text-[12px] font-medium text-red-dark">carries the call to action</span> : null}
                      </p>
                      <p className="text-[14px] text-gray-700">{b.purpose}</p>
                      <p className="text-[13px] text-gray">Feeling: {b.emotion}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          </div>
          <div className="grid gap-8 border-t border-gray-100 px-6 py-6 lg:grid-cols-2">
            <div>
              <h3 className="mb-3 text-[14px] font-semibold">Characters</h3>
              {plan.characters?.length ? (
                <ul className="space-y-3">
                  {plan.characters.map((c) => (
                    <li key={c.id} className="rounded-lg border border-gray-200 px-4 py-3">
                      <p className="text-[15px] font-semibold">
                        {c.name} <span className="font-normal text-gray">({c.role})</span>
                      </p>
                      <p className="mt-1 text-[14px]">{c.appearance}</p>
                      <p className="text-[13px] text-gray">Wardrobe: {c.wardrobe}</p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[14px] text-gray">No recurring characters.</p>
              )}
            </div>
            <div>
              <h3 className="mb-3 text-[14px] font-semibold">Locations</h3>
              <ul className="space-y-3">
                {(plan.locations ?? []).map((l) => (
                  <li key={l.id} className="rounded-lg border border-gray-200 px-4 py-3">
                    <p className="text-[15px] font-semibold">{l.name}</p>
                    <p className="mt-1 text-[14px]">{l.description}</p>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>
      ) : (
        <EmptyState title="No creative plan yet">The creative stage writes it after intake.</EmptyState>
      )}
    </div>
  );
}
