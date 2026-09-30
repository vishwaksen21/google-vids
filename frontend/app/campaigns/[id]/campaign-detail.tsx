"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Archive, ArchiveRestore, Clapperboard, Pencil } from "lucide-react";
import { useRef, useState } from "react";
import useSWR from "swr";
import { BriefForm } from "@/components/brief-form";
import { JobRow } from "@/components/job-row";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { TextField } from "@/components/ui/field";
import { EmptyState, ErrorNotice, Loading, Notice } from "@/components/ui/notice";
import { KeyValues, PageHeader, Section } from "@/components/ui/section";
import { StatusBadge } from "@/components/ui/status-badge";
import { ApiError, apiRequest, artifactContentUrl } from "@/lib/api";
import { formFromBrief } from "@/lib/brief";
import { formatDateTime } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import { ACTIVE_JOB_STATUSES, humanise } from "@/lib/status";
import type { BriefInput, BriefVersion, Campaign, JobSummary } from "@/lib/types";

export function BriefView({ brief }: { brief: Partial<BriefInput> | null }) {
  if (!brief) return <p className="text-[14px] text-gray">No brief saved.</p>;
  const o = brief.output;
  const structured = (["objective", "audience", "product", "key_message", "cta", "tone"] as const).map((k) => ({
    label: humanise(k === "cta" ? "call_to_action" : k),
    value: brief[k] ? String(brief[k]) : <span className="text-gray">Inferred from the brief</span>,
  }));
  return (
    <div className="space-y-6">
      <blockquote className="border-l-2 border-red pl-4 text-[16px] leading-relaxed whitespace-pre-line text-ink">{brief.raw_brief}</blockquote>
      <KeyValues items={structured} columns={3} />
      <div className="grid gap-6 md:grid-cols-2">
        <div>
          <h3 className="mb-2 text-[14px] font-semibold text-ink">Required claims</h3>
          {brief.required_claims?.length ? (
            <ul className="space-y-2 text-[14px]">
              {brief.required_claims.map((c, i) => (
                <li key={i} className="rounded-md border border-gray-200 px-3 py-2">
                  {c.text}
                  <span className="mt-0.5 block text-[12.5px] text-gray">{c.fact_id ? `Approved fact ${c.fact_id}` : "No approved fact cited"}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[14px] text-gray">None</p>
          )}
        </div>
        <div>
          <h3 className="mb-2 text-[14px] font-semibold text-ink">Disclosures</h3>
          {brief.disclosures?.length ? (
            <ul className="space-y-2 text-[14px]">
              {brief.disclosures.map((d, i) => (
                <li key={i} className="rounded-md border border-gray-200 px-3 py-2">
                  {d.text}
                  <span className="mt-0.5 block text-[12.5px] text-gray">
                    {[d.on_screen ? "on screen" : null, d.spoken ? "spoken" : null].filter(Boolean).join(" and ") || "not placed"}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[14px] text-gray">None added</p>
          )}
        </div>
      </div>
      {o ? (
        <KeyValues
          columns={4}
          items={[
            { label: "Aspect ratio", value: o.aspect_ratio },
            {
              label: "Duration",
              value:
                o.min_duration_seconds || o.max_duration_seconds
                  ? `${o.min_duration_seconds ?? "any"} to ${o.max_duration_seconds ?? "any"} s`
                  : "Planner decides",
            },
            { label: "Language", value: o.language },
            {
              label: "Includes",
              value: [o.captions_required ? "captions" : null, o.voiceover_required ? "voiceover" : null, o.end_card_required ? "end card" : null].filter(Boolean).join(", ") || "none",
            },
          ]}
        />
      ) : null}
      {brief.mandatory_elements?.length ? (
        <div>
          <h3 className="mb-2 text-[14px] font-semibold text-ink">Mandatory elements</h3>
          <ul className="list-disc space-y-1 pl-5 text-[14px]">
            {brief.mandatory_elements.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {brief.reference_asset_ids?.length ? (
        <div>
          <h3 className="mb-2 text-[14px] font-semibold text-ink">Reference images</h3>
          <ul className="grid grid-cols-3 gap-3 sm:grid-cols-7">
            {brief.reference_asset_ids.map((id, i) => (
              <li key={id}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={artifactContentUrl(id)} alt={`Reference image ${i + 1}`} className="aspect-square w-full rounded-md border border-gray-200 object-cover" />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function CampaignDetail({ id }: { id: string }) {
  const router = useRouter();
  const campaign = useSWR<Campaign>(`campaigns/${id}`);
  const jobs = useSWR<JobSummary[]>(`campaigns/${id}/jobs`, { refreshInterval: 10000 });
  const briefs = useSWR<BriefVersion[]>(`campaigns/${id}/briefs`);
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [activeConflict, setActiveConflict] = useState<string | null>(null);
  const keyRef = useRef<string | null>(null);

  const c = campaign.data;
  const activeJob = (jobs.data ?? []).find((j) => ACTIVE_JOB_STATUSES.has(j.status));

  const start = useAction(async () => {
    setActiveConflict(null);
    keyRef.current ??= newKey();
    try {
      const job = await apiRequest<JobSummary>(`campaigns/${id}/jobs`, { method: "POST", headers: { "idempotency-key": keyRef.current } });
      keyRef.current = null;
      router.push(`/jobs/${job.id}`);
      return job;
    } catch (e) {
      keyRef.current = null;
      if (e instanceof ApiError && e.status === 409) {
        const list = await jobs.mutate();
        const act = (list ?? []).find((j) => ACTIVE_JOB_STATUSES.has(j.status));
        if (act) setActiveConflict(act.id);
      }
      throw e;
    }
  });

  const rename = useAction(async () => {
    if (!c) return;
    const updated = await apiRequest<Campaign>(`campaigns/${id}`, { method: "PATCH", body: { name: newName.trim(), expected_row_version: c.row_version } });
    await campaign.mutate(updated, { revalidate: false });
    setRenameOpen(false);
  });

  const archive = useAction(async (restore: boolean) => {
    const updated = await apiRequest<Campaign>(`campaigns/${id}/${restore ? "unarchive" : "archive"}`, { method: "POST" });
    await campaign.mutate(updated, { revalidate: false });
    setArchiveOpen(false);
  });

  if (campaign.error) {
    return <ErrorNotice error={campaign.error} title="The campaign could not be loaded" onRetry={() => campaign.mutate()} />;
  }
  if (!c) return <Loading label="Loading campaign" lines={5} />;

  const archived = Boolean(c.archived_at);

  return (
    <>
      <PageHeader
        meta={
          <>
            <Link href="/campaigns" className="font-medium text-red-dark underline-offset-2 hover:underline">
              Campaigns
            </Link>
            <span aria-hidden>/</span>
            <span>{humanise(c.status)}</span>
            {archived ? <StatusBadge kind="stage" value="CANCELLED" label="Archived" size="sm" /> : null}
          </>
        }
        title={c.name}
        lead={`Owned by ${c.owner}. Brief v${c.current_brief_version}, last updated ${formatDateTime(c.updated_at)}.`}
        actions={
          <>
            <Button
              size="sm"
              icon={<Pencil aria-hidden className="size-4" />}
              onClick={() => {
                setNewName(c.name);
                rename.reset();
                setRenameOpen(true);
              }}
            >
              Rename
            </Button>
            <Button size="sm" icon={archived ? <ArchiveRestore aria-hidden className="size-4" /> : <Archive aria-hidden className="size-4" />} onClick={() => (archived ? void archive.run(true) : setArchiveOpen(true))} loading={archived && archive.pending}>
              {archived ? "Restore" : "Archive"}
            </Button>
          </>
        }
      />

      <ErrorNotice error={archived ? archive.error : null} className="mb-6" />

      <section aria-labelledby="start-heading" className="mb-8 rounded-2xl border border-gray-200 bg-white p-6">
        <div className="flex flex-wrap items-center justify-between gap-6">
          <div className="max-w-2xl">
            <h2 id="start-heading" className="text-[20px] font-semibold text-ink">
              {activeJob ? "A production is in progress" : "Start production"}
            </h2>
            <p className="mt-1 text-[15px] text-gray">
              {activeJob
                ? "Only one production can run per campaign. Follow it in the monitor."
                : `Plans the film from brief v${c.current_brief_version}, then waits for you to approve the plan before anything is generated in Google Vids.`}
            </p>
          </div>
          {activeJob ? (
            <Link href={`/jobs/${activeJob.id}`} className="inline-flex h-12 items-center gap-2 rounded-lg bg-red px-6 text-[15px] font-semibold text-white hover:bg-red-dark">
              <Clapperboard aria-hidden className="size-5" /> Open the monitor
            </Link>
          ) : (
            <Button variant="primary" className="h-12 rounded-lg px-6 text-[15px] font-semibold" loading={start.pending} disabled={archived} onClick={() => void start.run()} icon={<Clapperboard aria-hidden className="size-5" />}>
              {start.pending ? "Starting production" : "Start production"}
            </Button>
          )}
        </div>
        {archived ? <p className="mt-3 text-[14px] text-gray">Restore the campaign to start a production.</p> : null}
        <ErrorNotice error={start.error} className="mt-4" />
        {activeConflict ? (
          <p className="mt-2 text-[14px]">
            <Link href={`/jobs/${activeConflict}`} className="font-medium text-red-dark underline-offset-2 hover:underline">
              Open the active production
            </Link>
          </p>
        ) : null}
      </section>

      <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-8">
          {saved ? <Notice title={saved} /> : null}
          <Section
            title={editing ? "Edit brief" : `Brief v${c.current_brief_version}`}
            description={editing ? "Saving creates a new version. A running production keeps the version it started with." : undefined}
            actions={
              !editing && !archived ? (
                <Button size="sm" icon={<Pencil aria-hidden className="size-4" />} onClick={() => { setSaved(null); setEditing(true); }}>
                  Edit brief
                </Button>
              ) : null
            }
          >
            {editing ? (
              <BriefForm
                initial={formFromBrief(c.name, c.brief as Partial<BriefInput>)}
                withName={false}
                brandProfileId={c.brand_profile_id}
                submitLabel="Save as new version"
                pendingLabel="Saving"
                onCancel={() => setEditing(false)}
                onSubmit={async (_name, brief) => {
                  const updated = await apiRequest<Campaign>(`campaigns/${id}/brief`, { method: "PUT", body: { brief } });
                  await campaign.mutate(updated, { revalidate: false });
                  void briefs.mutate();
                  setEditing(false);
                  setSaved(`Brief v${updated.current_brief_version} saved`);
                  return updated;
                }}
              />
            ) : (
              <BriefView brief={c.brief as Partial<BriefInput>} />
            )}
          </Section>

          <Section title="Productions" description="Every production made from this campaign, newest first." className="overflow-hidden">
            <div className="-mx-5 -my-4">
              <ErrorNotice error={jobs.error} onRetry={() => jobs.mutate()} className="m-5" />
              {!jobs.data && !jobs.error ? (
                <div className="px-5 py-4">
                  <Loading label="Loading productions" lines={2} />
                </div>
              ) : null}
              {jobs.data?.length === 0 ? (
                <div className="p-5">
                  <EmptyState title="No productions yet">Start one above. It will be listed here with its status.</EmptyState>
                </div>
              ) : null}
              {jobs.data?.length ? (
                <ul className="divide-y divide-gray-100">
                  {jobs.data.map((j) => (
                    <JobRow key={j.id} job={j} showCampaign={false} />
                  ))}
                </ul>
              ) : null}
            </div>
          </Section>
        </div>

        <aside aria-label="Brief versions">
          <Section title="Version history" headingLevel={2}>
            <ErrorNotice error={briefs.error} onRetry={() => briefs.mutate()} />
            {!briefs.data && !briefs.error ? <Loading lines={2} /> : null}
            <ol className="space-y-2">
              {(briefs.data ?? []).map((b) => (
                <li key={b.id}>
                  <details className="group rounded-lg border border-gray-200 px-3 py-2 open:bg-gray-50">
                    <summary className="flex items-center justify-between gap-2 text-[14px]">
                      <span className="font-medium text-ink">
                        Version {b.version}
                        {b.version === c.current_brief_version ? <span className="ml-2 text-[12px] font-normal text-red-dark">current</span> : null}
                      </span>
                      <span className="text-[12.5px] text-gray tabular">{formatDateTime(b.created_at)}</span>
                    </summary>
                    <p className="mt-1 text-[12.5px] text-gray">Saved by {b.created_by}</p>
                    <p className="mt-2 line-clamp-6 text-[13px] whitespace-pre-line text-ink">{String(b.content.raw_brief ?? "")}</p>
                  </details>
                </li>
              ))}
            </ol>
          </Section>
        </aside>
      </div>

      <Dialog
        open={renameOpen}
        onClose={() => setRenameOpen(false)}
        title="Rename campaign"
        footer={
          <>
            <Button onClick={() => setRenameOpen(false)}>Cancel</Button>
            <Button variant="primary" loading={rename.pending} disabled={newName.trim().length < 2 || newName.trim().length > 120} onClick={() => void rename.run()}>
              Save name
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <TextField label="Campaign name" value={newName} onChange={(e) => setNewName(e.target.value)} maxLength={120} hint="2 to 120 characters." autoFocus />
          <ErrorNotice error={rename.error} />
        </div>
      </Dialog>

      <Dialog
        open={archiveOpen}
        onClose={() => setArchiveOpen(false)}
        title="Archive this campaign?"
        description="Archived campaigns are hidden from lists and cannot start productions. You can restore it later."
        footer={
          <>
            <Button onClick={() => setArchiveOpen(false)}>Keep it</Button>
            <Button variant="primary" loading={archive.pending} onClick={() => void archive.run(false)}>
              Archive campaign
            </Button>
          </>
        }
      >
        <ErrorNotice error={archive.error} />
      </Dialog>
    </>
  );
}
