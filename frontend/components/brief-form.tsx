"use client";

import { ArrowDown, ArrowUp, ChevronDown, ImagePlus, Plus, Trash2 } from "lucide-react";
import { useId, useRef, useState } from "react";
import useSWR from "swr";
import { ApiError, apiRequest, artifactContentUrl, errorMessage } from "@/lib/api";
import {
  briefPayload,
  checkReferenceFile,
  disclosureSeconds,
  emptyScene,
  MAX_CLAIMS,
  MAX_DISCLOSURES,
  MAX_MANDATORY,
  MAX_REFERENCES,
  MAX_SCENES,
  SCENE_SECONDS,
  validateBriefForm,
  type BriefErrors,
  type BriefFormState,
  type ReferenceImage,
  type SceneForm,
} from "@/lib/brief";
import { formatBytes } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import type { BrandProfile, BriefInput } from "@/lib/types";
import { Button } from "./ui/button";
import { Checkbox, SelectField, TextArea, TextField } from "./ui/field";
import { ErrorNotice, Notice } from "./ui/notice";

function Fieldset({ legend, description, children }: { legend: string; description?: string; children: React.ReactNode }) {
  const id = useId();
  return (
    <div role="group" aria-labelledby={id} className="grid gap-x-10 gap-y-4 border-t border-gray-200 pt-8 lg:grid-cols-[240px_minmax(0,1fr)]">
      <div>
        <h2 id={id} className="text-[16px] font-semibold text-ink">
          {legend}
        </h2>
        {description ? <p className="mt-1 text-[14px] text-gray">{description}</p> : null}
      </div>
      <div className="min-w-0 space-y-5">{children}</div>
    </div>
  );
}

const STRUCTURED: { key: "objective" | "audience" | "product" | "key_message" | "cta" | "tone"; label: string; hint: string }[] = [
  { key: "objective", label: "Objective", hint: "What the video must achieve." },
  { key: "audience", label: "Audience", hint: "Who it is for." },
  { key: "product", label: "Product", hint: "The product or plan featured." },
  { key: "key_message", label: "Key message", hint: "The one thing viewers should remember." },
  { key: "cta", label: "Call to action", hint: "What viewers should do next." },
  { key: "tone", label: "Tone", hint: "Leave empty to use the brand profile's tone." },
];

const ESSENTIAL_ERRORS = new Set(["name", "brief.raw_brief", "brief.reference_asset_ids", "brief.output.aspect_ratio"]);

function isMoreOptionsError(path: string): boolean {
  return !ESSENTIAL_ERRORS.has(path) && !path.startsWith("brief.scenes");
}

function usesMoreOptions(f: BriefFormState): boolean {
  return (
    STRUCTURED.some((field) => f[field.key].trim() !== "") ||
    f.claims.length > 0 ||
    f.disclosures.length > 0 ||
    f.mandatory_elements.length > 0 ||
    f.min_duration.trim() !== "" ||
    f.max_duration.trim() !== ""
  );
}

export function BriefForm({
  initial,
  withName,
  brandProfileId,
  submitLabel,
  pendingLabel,
  onSubmit,
  onCancel,
}: {
  initial: BriefFormState;
  withName: boolean;
  brandProfileId?: string;
  submitLabel: string;
  pendingLabel?: string;
  onSubmit: (name: string, brief: BriefInput) => Promise<unknown>;
  onCancel?: () => void;
}) {
  const [f, setF] = useState<BriefFormState>(initial);
  const [errors, setErrors] = useState<BriefErrors>({});
  // The optional sections start closed for a new brief and open when a brief already uses them.
  const [more, setMore] = useState(() => usesMoreOptions(initial));
  const moreId = useId();
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);
  const profiles = useSWR<BrandProfile[]>("brand-profiles");
  const profile = profiles.data?.find((p) => p.id === brandProfileId) ?? profiles.data?.[0];
  const facts = profile?.approved_facts ?? [];
  // A regulated profile's standard disclosures are shown on the last written scene.
  const disclosureNeed = profile?.regulated
    ? Math.max(0, ...(profile.standard_disclosures ?? []).map(disclosureSeconds), ...f.disclosures.map((d) => disclosureSeconds(d.text)))
    : 0;

  const set = <K extends keyof BriefFormState>(key: K, value: BriefFormState[K]) => setF((s) => ({ ...s, [key]: value }));
  const err = (path: string) => errors[path];

  const submit = useAction(async () => {
    try {
      return await onSubmit(f.name.trim(), briefPayload(f));
    } catch (e) {
      if (e instanceof ApiError && e.fieldErrors.length) {
        const mapped: BriefErrors = {};
        for (const fe of e.fieldErrors) mapped[fe.path] = fe.message;
        setErrors(mapped);
        if (Object.keys(mapped).some(isMoreOptionsError)) setMore(true);
      }
      throw e;
    }
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const found = validateBriefForm(f, { requireName: withName });
    setErrors(found);
    if (Object.keys(found).some(isMoreOptionsError)) setMore(true);
    if (Object.keys(found).length) {
      requestAnimationFrame(() => formRef.current?.querySelector<HTMLElement>("[aria-invalid='true']")?.focus());
      return;
    }
    void submit.run();
  };

  const uploadFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploadError(null);
    let count = f.references.length;
    for (const file of Array.from(files)) {
      const problem = checkReferenceFile(file, count);
      if (problem) {
        setUploadError(`${file.name}: ${problem}`);
        continue;
      }
      count += 1;
      setUploading((n) => n + 1);
      try {
        const form = new FormData();
        form.append("file", file);
        const ref = await apiRequest<ReferenceImage>("references", { method: "POST", body: form });
        setF((s) => ({ ...s, references: [...s.references, ref] }));
      } catch (e) {
        count -= 1;
        setUploadError(`${file.name}: ${errorMessage(e)}`);
      } finally {
        setUploading((n) => n - 1);
      }
    }
  };

  const errorCount = Object.keys(errors).length;

  return (
    <form ref={formRef} onSubmit={handleSubmit} noValidate className="space-y-8">
      {errorCount ? (
        <Notice tone="error" title={`Check ${errorCount} field${errorCount === 1 ? "" : "s"}`}>
          The brief was not {withName ? "created" : "saved"}. Each problem is shown next to its field.
        </Notice>
      ) : null}

      <Fieldset legend="Your video" description="Describe the video in a few sentences: what it is about, who it is for and what it should say. You approve the plan before anything is made.">
        {withName ? (
          <TextField label="Campaign name" name="name" value={f.name} onChange={(e) => set("name", e.target.value)} error={err("name")} maxLength={120} required />
        ) : null}
        <TextArea
          label="Brief"
          name="raw_brief"
          rows={7}
          value={f.raw_brief}
          onChange={(e) => set("raw_brief", e.target.value)}
          error={err("brief.raw_brief")}
          hint={`${f.raw_brief.trim().length} of 4000 characters`}
          placeholder="A 30-second film introducing our family floater health plan to parents in their thirties…"
          required
        />
              <div role="radiogroup" aria-labelledby="aspect-label">
          <p id="aspect-label" className="mb-1.5 text-[14px] font-medium text-ink">
            Video shape
          </p>
          <div className="flex flex-wrap gap-3">
            {(
              [
                ["16:9", "Landscape (16:9)"],
                ["9:16", "Portrait (9:16)"],
              ] as const
            ).map(([value, label]) => (
              <label
                key={value}
                className={`flex h-10 cursor-pointer items-center gap-2 rounded-md border px-3 text-[14px] ${f.aspect_ratio === value ? "border-red bg-red-50 text-red-deep" : "border-gray-300 text-ink hover:border-gray-400"}`}
              >
                <input type="radio" name="aspect_ratio" value={value} checked={f.aspect_ratio === value} onChange={() => set("aspect_ratio", value)} />
                {label}
              </label>
            ))}
          </div>
        </div>
      </Fieldset>

      <Fieldset legend="Photos" description={`Optional. Up to ${MAX_REFERENCES} PNG or JPEG images, 10 MB each, used when the video is generated.`}>
        {uploadError ? <Notice tone="error">{uploadError}</Notice> : null}
        {err("brief.reference_asset_ids") ? <Notice tone="error">{err("brief.reference_asset_ids")}</Notice> : null}
        {f.references.length ? (
          <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {f.references.map((r) => (
              <li key={r.id} className="rounded-lg border border-gray-200 p-2">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={artifactContentUrl(r.id)} alt={`Reference image ${r.name}`} className="aspect-square w-full rounded-md bg-gray-50 object-cover" />
                <p className="mt-1.5 truncate text-[12px] text-ink" title={r.name}>
                  {r.name}
                </p>
                <div className="flex items-center justify-between text-[12px] text-gray">
                  <span>{r.size_bytes ? formatBytes(r.size_bytes) : "uploaded"}</span>
                  <button type="button" className="rounded px-1 font-medium text-red-dark hover:bg-red-50" onClick={() => set("references", f.references.filter((x) => x.id !== r.id))}>
                    Remove<span className="visually-hidden"> {r.name}</span>
                  </button>
                </div>
              </li>
            ))}
          </ul>
        ) : null}
        <label className={`inline-flex h-10 cursor-pointer items-center gap-2 rounded-md border border-dashed px-4 text-[14px] font-medium ${f.references.length >= MAX_REFERENCES ? "cursor-not-allowed border-gray-200 text-gray" : "border-gray-400 text-ink hover:border-red hover:text-red-dark"}`}>
          <ImagePlus aria-hidden className="size-4" />
          {uploading ? `Uploading ${uploading}…` : "Add photos"}
          <input
            type="file"
            accept="image/png,image/jpeg"
            multiple
            className="visually-hidden"
            disabled={f.references.length >= MAX_REFERENCES}
            onChange={(e) => {
              void uploadFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </label>
      </Fieldset>

      <Fieldset
        legend="Your scenes"
        description="Optional. Write each scene yourself and it is used word for word, with no AI planning. Each scene is one Google Vids clip of 3 to 10 seconds."
      >
        {f.scenes.length === 0 ? (
          <p className="text-[14px] text-gray">No scenes yet. Without scenes, the planner writes them from your description.</p>
        ) : null}
        {err("brief.scenes") ? <Notice tone="error">{err("brief.scenes")}</Notice> : null}
        {f.scenes.map((sc, i) => {
          const update = (patch: Partial<SceneForm>) => set("scenes", f.scenes.map((x, j) => (j === i ? { ...x, ...patch } : x)));
          const move = (to: number) => {
            const next = [...f.scenes];
            const [item] = next.splice(i, 1);
            next.splice(to, 0, item);
            set("scenes", next);
          };
          return (
            <div key={i} className="space-y-4 rounded-lg border border-gray-200 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-[15px] font-semibold text-ink">Scene {i + 1}</h3>
                <div className="flex flex-wrap gap-1">
                  <Button size="sm" variant="tertiary" disabled={i === 0} onClick={() => move(i - 1)} icon={<ArrowUp aria-hidden className="size-4" />}>
                    Up<span className="visually-hidden"> (scene {i + 1})</span>
                  </Button>
                  <Button size="sm" variant="tertiary" disabled={i === f.scenes.length - 1} onClick={() => move(i + 1)} icon={<ArrowDown aria-hidden className="size-4" />}>
                    Down<span className="visually-hidden"> (scene {i + 1})</span>
                  </Button>
                  <Button size="sm" variant="tertiary" onClick={() => set("scenes", f.scenes.filter((_, j) => j !== i))} icon={<Trash2 aria-hidden className="size-4" />}>
                    Remove<span className="visually-hidden"> scene {i + 1}</span>
                  </Button>
                </div>
              </div>
              <TextArea
                label={`Scene ${i + 1}: what Google Vids should show`}
                rows={3}
                value={sc.prompt}
                maxLength={4000}
                error={err(`brief.scenes.${i}.prompt`)}
                onChange={(e) => update({ prompt: e.target.value })}
                hint={
                  sc.photo_id
                    ? "The photo is attached first. Write what the person does and says, for example: speaks directly to the camera in a warm voice and says: “…”"
                    : "For example: A calm sunlit family kitchen, slow push in, natural morning light."
                }
              />
              <TextArea
                label={`Scene ${i + 1}: words on screen`}
                optional
                rows={2}
                value={sc.on_screen_text}
                maxLength={300}
                error={err(`brief.scenes.${i}.on_screen_text`)}
                onChange={(e) => update({ on_screen_text: e.target.value })}
                hint="Shown beside the clip for the whole scene."
              />
              <div className="grid gap-4 sm:grid-cols-2">
                <SelectField
                  label={`Scene ${i + 1}: length`}
                  value={String(sc.duration)}
                  error={err(`brief.scenes.${i}.duration_seconds`)}
                  onChange={(e) => update({ duration: Number(e.target.value) })}
                >
                  {Array.from({ length: SCENE_SECONDS.max - SCENE_SECONDS.min + 1 }, (_, k) => SCENE_SECONDS.min + k).map((n) => (
                    <option key={n} value={n}>
                      {n} seconds
                    </option>
                  ))}
                </SelectField>
                <SelectField
                  label={`Scene ${i + 1}: photo`}
                  optional
                  value={sc.photo_id}
                  error={err(`brief.scenes.${i}.photo_asset_id`)}
                  onChange={(e) => update({ photo_id: e.target.value })}
                  hint={f.references.length ? "The person in the photo appears in this scene." : "Add photos above to use one here."}
                >
                  <option value="">No photo</option>
                  {f.references.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.name}
                    </option>
                  ))}
                </SelectField>
              </div>
            </div>
          );
        })}
        <Button
          size="sm"
          disabled={f.scenes.length >= MAX_SCENES}
          onClick={() =>
            setF((s) => ({
              ...s,
              scenes: [...s.scenes, emptyScene()],
              // Google Vids cannot add these automatically yet; a written-scene production needs them off.
              ...(s.scenes.length === 0 ? { captions_required: false, voiceover_required: false, end_card_required: false } : {}),
            }))
          }
          icon={<Plus aria-hidden className="size-4" />}
        >
          Add a scene
        </Button>
        {f.scenes.length ? (
          <Notice tone="info">
            Captions, voiceover and the end card are switched off for written scenes: Google Vids cannot add them automatically yet.
            {disclosureNeed ? (
              <>
                {" "}
                Your brand&apos;s standard disclosure is shown on the last scene and needs {Math.ceil(disclosureNeed)} seconds, so make the last scene at
                least {Math.ceil(disclosureNeed)} seconds long.
              </>
            ) : null}
          </Notice>
        ) : null}
      </Fieldset>

      <div className="border-t border-gray-200 pt-6">
        <button
          type="button"
          aria-expanded={more}
          aria-controls={moreId}
          onClick={() => setMore((v) => !v)}
          className="inline-flex items-center gap-2 rounded-md text-[15px] font-semibold text-ink hover:text-red-dark"
        >
          <ChevronDown aria-hidden className={`size-4 transition-transform ${more ? "rotate-180" : ""}`} />
          More options
        </button>
        <p className="mt-1 text-[14px] text-gray">Details, claims, disclosures, required elements, length and captions. All optional.</p>
      </div>

      {more ? (
        <div id={moreId} className="space-y-8">
      <Fieldset legend="Details" description="Optional. Anything you fill in is used as given; anything left empty is worked out from your description.">
        <div className="grid gap-5 sm:grid-cols-2">
          {STRUCTURED.map((field) => (
            <TextField
              key={field.key}
              label={field.label}
              optional
              name={field.key}
              value={f[field.key]}
              onChange={(e) => set(field.key, e.target.value)}
              error={err(`brief.${field.key}`)}
              hint={field.hint}
              maxLength={300}
            />
          ))}
        </div>
      </Fieldset>

      <Fieldset
        legend="Claims"
        description="Every factual claim must rest on an approved fact from the brand profile. Claims without one are flagged in validation."
      >
        {profiles.error ? <ErrorNotice error={profiles.error} title="Approved facts could not be loaded" /> : null}
        {f.claims.length === 0 ? <p className="text-[14px] text-gray">No required claims.</p> : null}
        {f.claims.map((c, i) => (
          <div key={i} className="grid gap-3 rounded-lg border border-gray-200 p-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,260px)_auto] sm:items-start">
            <TextField
              label={`Claim ${i + 1}`}
              value={c.text}
              maxLength={300}
              error={err(`brief.required_claims.${i}.text`)}
              onChange={(e) => set("claims", f.claims.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
            />
            <SelectField
              label="Approved fact"
              value={c.fact_id}
              error={err(`brief.required_claims.${i}.fact_id`)}
              onChange={(e) => {
                const fact = facts.find((x) => x.fact_id === e.target.value);
                set(
                  "claims",
                  f.claims.map((x, j) => (j === i ? { fact_id: e.target.value, text: x.text.trim() ? x.text : fact?.text ?? "" } : x)),
                );
              }}
            >
              <option value="">No fact (will be flagged)</option>
              {facts.map((fact) => (
                <option key={fact.fact_id} value={fact.fact_id}>
                  {fact.fact_id}: {fact.text.slice(0, 60)}
                  {fact.text.length > 60 ? "…" : ""}
                </option>
              ))}
            </SelectField>
            <Button
              size="sm"
              variant="tertiary"
              className="sm:mt-7"
              onClick={() => set("claims", f.claims.filter((_, j) => j !== i))}
              icon={<Trash2 aria-hidden className="size-4" />}
            >
              Remove<span className="visually-hidden"> claim {i + 1}</span>
            </Button>
          </div>
        ))}
        <Button
          size="sm"
          disabled={f.claims.length >= MAX_CLAIMS}
          onClick={() => set("claims", [...f.claims, { text: "", fact_id: "" }])}
          icon={<Plus aria-hidden className="size-4" />}
        >
          Add a claim
        </Button>
      </Fieldset>

      <Fieldset legend="Disclosures" description="Regulatory text that must appear in the video, verbatim.">
        {f.disclosures.length === 0 ? <p className="text-[14px] text-gray">No disclosures added. The brand profile&apos;s standard disclosures still apply.</p> : null}
        {f.disclosures.map((d, i) => (
          <div key={i} className="space-y-3 rounded-lg border border-gray-200 p-4">
            <TextArea
              label={`Disclosure ${i + 1}`}
              rows={3}
              value={d.text}
              maxLength={4000}
              error={err(`brief.disclosures.${i}.text`)}
              onChange={(e) => set("disclosures", f.disclosures.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
            />
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
              <Checkbox label="Shown on screen" checked={d.on_screen} onChange={(e) => set("disclosures", f.disclosures.map((x, j) => (j === i ? { ...x, on_screen: e.target.checked } : x)))} />
              <Checkbox label="Spoken" checked={d.spoken} onChange={(e) => set("disclosures", f.disclosures.map((x, j) => (j === i ? { ...x, spoken: e.target.checked } : x)))} />
              <Button size="sm" variant="tertiary" className="ml-auto" onClick={() => set("disclosures", f.disclosures.filter((_, j) => j !== i))} icon={<Trash2 aria-hidden className="size-4" />}>
                Remove<span className="visually-hidden"> disclosure {i + 1}</span>
              </Button>
            </div>
          </div>
        ))}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={f.disclosures.length >= MAX_DISCLOSURES} onClick={() => set("disclosures", [...f.disclosures, { text: "", on_screen: true, spoken: false }])} icon={<Plus aria-hidden className="size-4" />}>
            Add a disclosure
          </Button>
          {(profile?.standard_disclosures ?? []).map((text, i) =>
            f.disclosures.some((d) => d.text.trim() === text.trim()) ? null : (
              <Button key={i} size="sm" variant="tertiary" disabled={f.disclosures.length >= MAX_DISCLOSURES} onClick={() => set("disclosures", [...f.disclosures, { text, on_screen: true, spoken: false }])}>
                Add standard disclosure {i + 1}
              </Button>
            ),
          )}
        </div>
      </Fieldset>

      <Fieldset legend="Mandatory elements" description="Anything that must appear, such as a product shot or a specific setting.">
        {f.mandatory_elements.map((m, i) => (
          <div key={i} className="flex items-start gap-2">
            <TextField
              className="flex-1"
              label={`Element ${i + 1}`}
              value={m}
              maxLength={300}
              error={err(`brief.mandatory_elements.${i}`)}
              onChange={(e) => set("mandatory_elements", f.mandatory_elements.map((x, j) => (j === i ? e.target.value : x)))}
            />
            <Button size="sm" variant="tertiary" className="mt-7" onClick={() => set("mandatory_elements", f.mandatory_elements.filter((_, j) => j !== i))} icon={<Trash2 aria-hidden className="size-4" />}>
              Remove<span className="visually-hidden"> element {i + 1}</span>
            </Button>
          </div>
        ))}
        <Button size="sm" disabled={f.mandatory_elements.length >= MAX_MANDATORY} onClick={() => set("mandatory_elements", [...f.mandatory_elements, ""])} icon={<Plus aria-hidden className="size-4" />}>
          Add an element
        </Button>
      </Fieldset>

      <Fieldset legend="Length and extras" description="Optional. Leave the durations empty and the planner sizes the video to the script.">
        <div className="grid gap-5 sm:grid-cols-3">
          <TextField label="Minimum duration (s)" optional inputMode="decimal" value={f.min_duration} onChange={(e) => set("min_duration", e.target.value)} error={err("brief.output.min_duration_seconds")} />
          <TextField label="Maximum duration (s)" optional inputMode="decimal" value={f.max_duration} onChange={(e) => set("max_duration", e.target.value)} error={err("brief.output.max_duration_seconds")} />
          <TextField label="Language" value={f.language} onChange={(e) => set("language", e.target.value)} error={err("brief.output.language")} maxLength={300} />
        </div>
        <div className="flex flex-wrap gap-x-8 gap-y-3">
          <Checkbox label="Captions" checked={f.captions_required} onChange={(e) => set("captions_required", e.target.checked)} />
          <Checkbox label="Voiceover" checked={f.voiceover_required} onChange={(e) => set("voiceover_required", e.target.checked)} />
          <Checkbox label="End card" checked={f.end_card_required} onChange={(e) => set("end_card_required", e.target.checked)} />
        </div>
      </Fieldset>

        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-3 border-t border-gray-200 pt-6">
        <ErrorNotice error={submit.error} className="w-full" />
        <Button type="submit" variant="primary" loading={submit.pending} disabled={uploading > 0}>
          {submit.pending ? pendingLabel ?? submitLabel : submitLabel}
        </Button>
        {onCancel ? (
          <Button onClick={onCancel} disabled={submit.pending}>
            Cancel
          </Button>
        ) : null}
      </div>
    </form>
  );
}
