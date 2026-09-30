"use client";

import Link from "next/link";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { SelectField, TextArea, TextField } from "@/components/ui/field";
import { ErrorNotice, Loading, Notice } from "@/components/ui/notice";
import { KeyValues, PageHeader, Section } from "@/components/ui/section";
import { apiRequest } from "@/lib/api";
import { IDENT } from "@/lib/brief";
import { formatDateTime } from "@/lib/format";
import { useAction, useSystem } from "@/lib/hooks";
import { clearOperator, isValidOperator, setOperator, useOperator } from "@/lib/operator";
import { humanise } from "@/lib/status";
import type { ApprovedFact, BrandProfile } from "@/lib/types";

function OperatorSettings() {
  const operator = useOperator();
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  const [saved, setSaved] = useState(false);
  const value = draft ?? operator ?? "";

  return (
    <Section title="Operator" description="Your name is sent with every change and recorded in the audit trail. It is stored in this browser only.">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (!isValidOperator(value)) {
            setError("Use letters, numbers, spaces and . @ ' - only (up to 80 characters).");
            return;
          }
          setOperator(value);
          setDraft(null);
          setSaved(true);
        }}
      >
        <TextField
          className="min-w-[260px] flex-1"
          label="Your name"
          autoComplete="name"
          maxLength={80}
          value={value}
          error={error}
          onChange={(e) => {
            setDraft(e.target.value);
            setError(undefined);
            setSaved(false);
          }}
        />
        <Button type="submit" variant="primary">
          Save name
        </Button>
        {operator ? (
          <Button
            onClick={() => {
              clearOperator();
              setDraft("");
              setSaved(false);
            }}
          >
            Clear
          </Button>
        ) : null}
      </form>
      {saved ? (
        <p role="status" className="mt-3 text-[14px] text-ink">
          Saved in this browser. Changes you make are now recorded as {operator}.
        </p>
      ) : null}
      {operator === null ? <p className="mt-3 text-[14px] text-red-dark">No name set. Approvals and production controls are blocked until you set one.</p> : null}
    </Section>
  );
}

function ConfigValue({ value }: { value: unknown }) {
  if (typeof value === "boolean") return <>{value ? "Yes" : "No"}</>;
  if (value === null || value === undefined || value === "") return <span className="text-gray">—</span>;
  return <>{String(value)}</>;
}

function EffectiveConfig() {
  const { data, error, mutate } = useSystem();
  if (error) return <ErrorNotice error={error} onRetry={() => mutate()} />;
  if (!data) return <Loading />;
  const s = data.settings as Record<string, unknown>;
  const flat = Object.entries(s).filter(([, v]) => typeof v !== "object" || v === null);
  const groups = Object.entries(s).filter(([, v]) => typeof v === "object" && v !== null) as [string, Record<string, unknown>][];
  return (
    <Section title="Effective configuration" description="Read-only. Set on the backend; secrets are never exposed here.">
      <div className="space-y-6">
        <KeyValues columns={3} items={flat.map(([k, v]) => ({ label: humanise(k), value: <ConfigValue value={v} /> }))} />
        {groups.map(([k, v]) => (
          <div key={k}>
            <h3 className="mb-2 text-[14px] font-semibold text-ink">{humanise(k)}</h3>
            <KeyValues columns={3} items={Object.entries(v).map(([kk, vv]) => ({ label: humanise(kk), value: <ConfigValue value={vv} /> }))} />
          </div>
        ))}
        <div>
          <h3 className="mb-2 text-[14px] font-semibold text-ink">Brand source</h3>
          <KeyValues
            columns={3}
            items={[
              { label: "DESIGN.md loaded", value: data.brand.available ? "Yes" : "No" },
              { label: "Digest", value: <code className="text-[12.5px] break-all">{data.brand.digest ?? "—"}</code> },
              { label: "Palette", value: data.brand.palette.join(", ") || "—" },
            ]}
          />
        </div>
      </div>
    </Section>
  );
}

interface Draft {
  tone: string;
  forbidden_terms: string;
  approved_facts: ApprovedFact[];
  standard_disclosures: string[];
  logo_position: string;
}

function toDraft(p: BrandProfile): Draft {
  return {
    tone: p.tone,
    forbidden_terms: p.forbidden_terms.join("\n"),
    approved_facts: p.approved_facts.map((f) => ({ fact_id: f.fact_id, text: f.text, source: f.source })),
    standard_disclosures: [...p.standard_disclosures],
    logo_position: p.logo_position,
  };
}

function validateDraft(d: Draft): Record<string, string> {
  const e: Record<string, string> = {};
  if (d.tone.length > 300) e.tone = "Keep the tone to 300 characters.";
  const terms = d.forbidden_terms.split("\n").map((t) => t.trim()).filter(Boolean);
  if (terms.length > 200) e.forbidden_terms = "At most 200 terms.";
  if (d.approved_facts.length > 200) e.facts = "At most 200 facts.";
  const ids = new Set<string>();
  d.approved_facts.forEach((f, i) => {
    if (!IDENT.test(f.fact_id)) e[`fact.${i}.fact_id`] = "Letters, numbers and . _ - only, up to 64 characters.";
    else if (ids.has(f.fact_id)) e[`fact.${i}.fact_id`] = "Fact ids must be unique.";
    ids.add(f.fact_id);
    if (!f.text.trim()) e[`fact.${i}.text`] = "Enter the fact.";
    if (!f.source.trim()) e[`fact.${i}.source`] = "Say where the fact comes from.";
  });
  if (d.standard_disclosures.length > 20) e.disclosures = "At most 20 disclosures.";
  d.standard_disclosures.forEach((t, i) => {
    if (!t.trim()) e[`disclosure.${i}`] = "Enter the disclosure or remove it.";
  });
  return e;
}

function BrandEditor({ profile, onSaved }: { profile: BrandProfile; onSaved: (p: BrandProfile) => void }) {
  const [d, setD] = useState<Draft>(() => toDraft(profile));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [confirm, setConfirm] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  const save = useAction(async () => {
    const body = {
      tone: d.tone.trim(),
      forbidden_terms: d.forbidden_terms.split("\n").map((t) => t.trim()).filter(Boolean),
      approved_facts: d.approved_facts.map((f) => ({ fact_id: f.fact_id.trim(), text: f.text.trim(), source: f.source.trim() })),
      standard_disclosures: d.standard_disclosures.map((t) => t.trim()),
      logo_position: d.logo_position,
    };
    const updated = await apiRequest<BrandProfile>(`brand-profiles/${profile.id}`, { method: "PATCH", body });
    setConfirm(false);
    setD(toDraft(updated));
    setSaved(`Saved. The brand profile was updated ${formatDateTime(updated.updated_at)}.`);
    onSaved(updated);
    return updated;
  });

  const setFact = (i: number, patch: Partial<ApprovedFact>) => setD((s) => ({ ...s, approved_facts: s.approved_facts.map((f, j) => (j === i ? { ...f, ...patch } : f)) }));

  return (
    <form
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        const found = validateDraft(d);
        setErrors(found);
        setSaved(null);
        if (!Object.keys(found).length) {
          save.reset();
          setConfirm(true);
        }
      }}
      className="space-y-6"
    >
      <Notice tone="attention" title="These settings govern compliance">
        Every future brief, script and final QA is checked against this profile: claims must cite an approved fact, forbidden terms block a stage, and standard disclosures must appear verbatim. Change
        them only with compliance sign-off.
      </Notice>
      {saved ? <Notice title={saved} /> : null}
      <KeyValues
        columns={3}
        items={[
          { label: "Profile", value: profile.name },
          { label: "Regulated", value: profile.regulated ? "Yes" : "No" },
          { label: "Last updated", value: formatDateTime(profile.updated_at) },
          { label: "Palette", value: profile.palette.join("; ") },
          { label: "Forbidden colours", value: profile.forbidden_colours.join(", ") },
        ]}
      />
      <TextField label="Tone" value={d.tone} maxLength={300} error={errors.tone} onChange={(e) => setD({ ...d, tone: e.target.value })} />
      <TextArea
        label="Forbidden terms"
        rows={6}
        value={d.forbidden_terms}
        error={errors.forbidden_terms}
        hint="One term or phrase per line."
        onChange={(e) => setD({ ...d, forbidden_terms: e.target.value })}
      />
      <div>
        <h3 className="mb-1 text-[14px] font-medium text-ink">Approved facts</h3>
        <p className="mb-3 text-[13px] text-gray">Claims in briefs and scripts may only rely on these. Each needs a stable id, the exact wording and its source.</p>
        {errors.facts ? <p className="mb-2 text-[13px] font-medium text-red-dark">{errors.facts}</p> : null}
        <ul className="space-y-3">
          {d.approved_facts.map((f, i) => (
            <li key={i} className="grid gap-3 rounded-lg border border-gray-200 p-4 md:grid-cols-[200px_minmax(0,1fr)]">
              <TextField label="Fact id" value={f.fact_id} error={errors[`fact.${i}.fact_id`]} onChange={(e) => setFact(i, { fact_id: e.target.value })} maxLength={64} />
              <TextArea label="Fact" rows={2} value={f.text} error={errors[`fact.${i}.text`]} onChange={(e) => setFact(i, { text: e.target.value })} />
              <TextField className="md:col-start-2" label="Source" value={f.source} error={errors[`fact.${i}.source`]} onChange={(e) => setFact(i, { source: e.target.value })} />
              <div className="md:col-start-2">
                <Button size="sm" variant="tertiary" icon={<Trash2 aria-hidden className="size-4" />} onClick={() => setD({ ...d, approved_facts: d.approved_facts.filter((_, j) => j !== i) })}>
                  Remove fact<span className="visually-hidden"> {f.fact_id}</span>
                </Button>
              </div>
            </li>
          ))}
        </ul>
        <Button size="sm" className="mt-3" icon={<Plus aria-hidden className="size-4" />} onClick={() => setD({ ...d, approved_facts: [...d.approved_facts, { fact_id: "", text: "", source: "" }] })}>
          Add a fact
        </Button>
      </div>
      <div>
        <h3 className="mb-1 text-[14px] font-medium text-ink">Standard disclosures</h3>
        {errors.disclosures ? <p className="mb-2 text-[13px] font-medium text-red-dark">{errors.disclosures}</p> : null}
        <ul className="space-y-3">
          {d.standard_disclosures.map((t, i) => (
            <li key={i} className="flex items-start gap-2">
              <TextArea
                className="flex-1"
                label={`Disclosure ${i + 1}`}
                rows={3}
                value={t}
                error={errors[`disclosure.${i}`]}
                onChange={(e) => setD({ ...d, standard_disclosures: d.standard_disclosures.map((x, j) => (j === i ? e.target.value : x)) })}
              />
              <Button size="sm" variant="tertiary" className="mt-7" icon={<Trash2 aria-hidden className="size-4" />} onClick={() => setD({ ...d, standard_disclosures: d.standard_disclosures.filter((_, j) => j !== i) })}>
                Remove<span className="visually-hidden"> disclosure {i + 1}</span>
              </Button>
            </li>
          ))}
        </ul>
        <Button size="sm" className="mt-3" icon={<Plus aria-hidden className="size-4" />} onClick={() => setD({ ...d, standard_disclosures: [...d.standard_disclosures, ""] })}>
          Add a disclosure
        </Button>
      </div>
      <SelectField label="Logo position on the end card" value={d.logo_position} onChange={(e) => setD({ ...d, logo_position: e.target.value })} className="max-w-xs">
        {["bottom-right", "bottom-left", "top-right", "top-left"].map((p) => (
          <option key={p} value={p}>
            {humanise(p.replace("-", "_"))}
          </option>
        ))}
      </SelectField>
      <div className="flex flex-wrap gap-2 border-t border-gray-200 pt-5">
        <Button type="submit" variant="primary">
          Save brand profile
        </Button>
        <Button
          onClick={() => {
            setD(toDraft(profile));
            setErrors({});
          }}
        >
          Discard changes
        </Button>
      </div>
      <Dialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Change the compliance rules?"
        description="Claims, forbidden terms and disclosures are checked against this profile. The change is recorded in the audit trail under your name."
        footer={
          <>
            <Button onClick={() => setConfirm(false)}>Cancel</Button>
            <Button variant="primary" loading={save.pending} onClick={() => void save.run()}>
              Save brand profile
            </Button>
          </>
        }
      >
        <ErrorNotice error={save.error} />
      </Dialog>
    </form>
  );
}

export function SettingsPage() {
  const profiles = useSWR<BrandProfile[]>("brand-profiles");
  return (
    <>
      <PageHeader
        meta={
          <Link href="/advanced" className="font-medium text-red-dark underline-offset-2 hover:underline">
            Advanced
          </Link>
        }
        title="Settings"
      />
      <div className="space-y-8">
        <OperatorSettings />
        <Section title="Brand profile" description="The rules every production is validated against.">
          {profiles.error ? <ErrorNotice error={profiles.error} onRetry={() => profiles.mutate()} /> : null}
          {!profiles.data && !profiles.error ? <Loading /> : null}
          {profiles.data?.length === 0 ? <p className="text-[14px] text-gray">No brand profile exists on the backend.</p> : null}
          {profiles.data?.map((p) => (
            <BrandEditor key={`${p.id}-${p.updated_at}`} profile={p} onSaved={() => void profiles.mutate()} />
          ))}
        </Section>
        <EffectiveConfig />
      </div>
    </>
  );
}
