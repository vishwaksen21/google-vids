// Brief form state, client-side validation mirroring BriefInput in the OpenAPI contract,
// and conversion to the request payload. The server re-validates; its 422s are shown too.

import type { BriefInput } from "./types";

export const SHORT_MAX = 300;
export const LONG_MAX = 4000;
export const MAX_CLAIMS = 20;
export const MAX_DISCLOSURES = 10;
export const MAX_MANDATORY = 20;
export const MAX_REFERENCES = 7;
export const MAX_REFERENCE_BYTES = 10 * 1024 * 1024;
export const MAX_SCENES = 10;
export const SCENE_SECONDS = { min: 3, max: 10, default: 8 } as const; // one generated clip
export const IDENT = /^[A-Za-z0-9_.-]{1,64}$/;

export const OPTIONAL_FIELDS = ["objective", "audience", "product", "key_message", "cta", "tone"] as const;
export type OptionalField = (typeof OPTIONAL_FIELDS)[number];

export interface ReferenceImage {
  id: string;
  name: string;
  size_bytes: number;
}

export interface SceneForm {
  prompt: string;
  on_screen_text: string;
  duration: number;
  photo_id: string;
}

export function emptyScene(): SceneForm {
  return { prompt: "", on_screen_text: "", duration: SCENE_SECONDS.default, photo_id: "" };
}

export interface BriefFormState {
  name: string;
  raw_brief: string;
  objective: string;
  audience: string;
  product: string;
  key_message: string;
  cta: string;
  tone: string;
  claims: { text: string; fact_id: string }[];
  disclosures: { text: string; on_screen: boolean; spoken: boolean }[];
  mandatory_elements: string[];
  references: ReferenceImage[];
  aspect_ratio: "16:9" | "9:16";
  min_duration: string;
  max_duration: string;
  language: string;
  captions_required: boolean;
  voiceover_required: boolean;
  end_card_required: boolean;
  scenes: SceneForm[];
}

export function emptyBriefForm(): BriefFormState {
  return {
    name: "",
    raw_brief: "",
    objective: "",
    audience: "",
    product: "",
    key_message: "",
    cta: "",
    tone: "",
    claims: [],
    disclosures: [],
    mandatory_elements: [],
    references: [],
    aspect_ratio: "16:9",
    min_duration: "",
    max_duration: "",
    language: "English",
    captions_required: true,
    voiceover_required: true,
    end_card_required: true,
    scenes: [],
  };
}

/** Pre-fills the form from a stored brief (for editing: saves a new version). */
export function formFromBrief(name: string, brief: Partial<BriefInput> | null | undefined): BriefFormState {
  const f = emptyBriefForm();
  f.name = name;
  if (!brief) return f;
  f.raw_brief = brief.raw_brief ?? "";
  for (const key of OPTIONAL_FIELDS) f[key] = (brief[key] as string | null | undefined) ?? "";
  f.claims = (brief.required_claims ?? []).map((c) => ({ text: c.text, fact_id: c.fact_id ?? "" }));
  f.disclosures = (brief.disclosures ?? []).map((d) => ({ text: d.text, on_screen: d.on_screen ?? true, spoken: d.spoken ?? false }));
  f.mandatory_elements = [...(brief.mandatory_elements ?? [])];
  f.references = (brief.reference_asset_ids ?? []).map((id) => ({ id, name: id, size_bytes: 0 }));
  const o = brief.output;
  if (o) {
    f.aspect_ratio = o.aspect_ratio === "9:16" ? "9:16" : "16:9";
    f.min_duration = o.min_duration_seconds != null ? String(o.min_duration_seconds) : "";
    f.max_duration = o.max_duration_seconds != null ? String(o.max_duration_seconds) : "";
    f.language = o.language ?? "English";
    f.captions_required = o.captions_required ?? true;
    f.voiceover_required = o.voiceover_required ?? true;
    f.end_card_required = o.end_card_required ?? true;
  }
  f.scenes = (brief.scenes ?? []).map((sc) => ({
    prompt: sc.prompt,
    on_screen_text: sc.on_screen_text ?? "",
    duration: sc.duration_seconds,
    photo_id: sc.photo_asset_id ?? "",
  }));
  return f;
}

export type BriefErrors = Record<string, string>;

function parseSeconds(value: string): number | null | "invalid" {
  const v = value.trim();
  if (!v) return null;
  const n = Number(v);
  if (!Number.isFinite(n)) return "invalid";
  return n;
}

/** Returns field-path → message. Paths match the server's (e.g. `brief.output.min_duration_seconds`). */
export function validateBriefForm(f: BriefFormState, opts: { requireName?: boolean } = {}): BriefErrors {
  const e: BriefErrors = {};
  const requireName = opts.requireName ?? true;
  if (requireName) {
    const n = f.name.trim();
    if (n.length < 2) e.name = "Enter a campaign name of at least 2 characters.";
    else if (n.length > 120) e.name = "Keep the campaign name to 120 characters or fewer.";
  }
  const raw = f.raw_brief.trim();
  if (!raw) e["brief.raw_brief"] = "Describe the campaign in a sentence or more.";
  else if (raw.length > LONG_MAX) e["brief.raw_brief"] = `Keep the brief to ${LONG_MAX} characters or fewer.`;
  for (const key of OPTIONAL_FIELDS) {
    if (f[key].trim().length > SHORT_MAX) e[`brief.${key}`] = `Keep this to ${SHORT_MAX} characters or fewer.`;
  }
  if (f.claims.length > MAX_CLAIMS) e["brief.required_claims"] = `At most ${MAX_CLAIMS} claims.`;
  f.claims.forEach((c, i) => {
    const t = c.text.trim();
    if (!t) e[`brief.required_claims.${i}.text`] = "Enter the claim text or remove the claim.";
    else if (t.length > SHORT_MAX) e[`brief.required_claims.${i}.text`] = `Keep claims to ${SHORT_MAX} characters.`;
    if (c.fact_id && !IDENT.test(c.fact_id)) e[`brief.required_claims.${i}.fact_id`] = "Choose an approved fact.";
  });
  if (f.disclosures.length > MAX_DISCLOSURES) e["brief.disclosures"] = `At most ${MAX_DISCLOSURES} disclosures.`;
  f.disclosures.forEach((d, i) => {
    const t = d.text.trim();
    if (!t) e[`brief.disclosures.${i}.text`] = "Enter the disclosure text or remove it.";
    else if (t.length > LONG_MAX) e[`brief.disclosures.${i}.text`] = `Keep disclosures to ${LONG_MAX} characters.`;
  });
  if (f.mandatory_elements.length > MAX_MANDATORY) e["brief.mandatory_elements"] = `At most ${MAX_MANDATORY} items.`;
  f.mandatory_elements.forEach((m, i) => {
    const t = m.trim();
    if (!t) e[`brief.mandatory_elements.${i}`] = "Enter the element or remove it.";
    else if (t.length > SHORT_MAX) e[`brief.mandatory_elements.${i}`] = `Keep this to ${SHORT_MAX} characters.`;
  });
  if (f.references.length > MAX_REFERENCES) e["brief.reference_asset_ids"] = `At most ${MAX_REFERENCES} reference images.`;
  const min = parseSeconds(f.min_duration);
  const max = parseSeconds(f.max_duration);
  if (min === "invalid" || (typeof min === "number" && (min < 1 || min > 600)))
    e["brief.output.min_duration_seconds"] = "Enter a number of seconds between 1 and 600.";
  if (max === "invalid" || (typeof max === "number" && (max < 1 || max > 600)))
    e["brief.output.max_duration_seconds"] = "Enter a number of seconds between 1 and 600.";
  if (typeof min === "number" && typeof max === "number" && !e["brief.output.min_duration_seconds"] && !e["brief.output.max_duration_seconds"] && max < min)
    e["brief.output.max_duration_seconds"] = "The maximum must be at least the minimum.";
  const lang = f.language.trim();
  if (!lang) e["brief.output.language"] = "Enter the language.";
  else if (lang.length > SHORT_MAX) e["brief.output.language"] = `Keep this to ${SHORT_MAX} characters.`;
  if (f.scenes.length > MAX_SCENES) e["brief.scenes"] = `At most ${MAX_SCENES} scenes.`;
  const photoIds = new Set(f.references.map((r) => r.id));
  f.scenes.forEach((sc, i) => {
    const p = sc.prompt.trim();
    if (!p) e[`brief.scenes.${i}.prompt`] = "Describe what this scene should show, or remove the scene.";
    else if (p.length > LONG_MAX) e[`brief.scenes.${i}.prompt`] = `Keep the description to ${LONG_MAX} characters.`;
    else if (sc.photo_id && p.includes("@"))
      e[`brief.scenes.${i}.prompt`] = "With a photo, describe the person as “the person in the photo”; don't type @.";
    if (sc.on_screen_text.trim().length > SHORT_MAX) e[`brief.scenes.${i}.on_screen_text`] = `Keep on-screen words to ${SHORT_MAX} characters.`;
    if (!(sc.duration >= SCENE_SECONDS.min && sc.duration <= SCENE_SECONDS.max))
      e[`brief.scenes.${i}.duration_seconds`] = `A scene is ${SCENE_SECONDS.min} to ${SCENE_SECONDS.max} seconds.`;
    if (sc.photo_id && !photoIds.has(sc.photo_id)) e[`brief.scenes.${i}.photo_asset_id`] = "Choose one of the uploaded photos.";
  });
  return e;
}

const orNull = (s: string) => (s.trim() ? s.trim() : null);

export function briefPayload(f: BriefFormState): BriefInput {
  const min = parseSeconds(f.min_duration);
  const max = parseSeconds(f.max_duration);
  return {
    raw_brief: f.raw_brief.trim(),
    objective: orNull(f.objective),
    audience: orNull(f.audience),
    product: orNull(f.product),
    key_message: orNull(f.key_message),
    cta: orNull(f.cta),
    tone: orNull(f.tone),
    required_claims: f.claims.map((c) => ({ text: c.text.trim(), fact_id: c.fact_id || null })),
    disclosures: f.disclosures.map((d) => ({ text: d.text.trim(), on_screen: d.on_screen, spoken: d.spoken })),
    mandatory_elements: f.mandatory_elements.map((m) => m.trim()),
    reference_asset_ids: f.references.map((r) => r.id),
    output: {
      aspect_ratio: f.aspect_ratio,
      min_duration_seconds: typeof min === "number" ? min : null,
      max_duration_seconds: typeof max === "number" ? max : null,
      language: f.language.trim(),
      captions_required: f.captions_required,
      voiceover_required: f.voiceover_required,
      end_card_required: f.end_card_required,
    },
    scenes: f.scenes.map((sc) => ({
      prompt: sc.prompt.trim(),
      on_screen_text: orNull(sc.on_screen_text),
      duration_seconds: sc.duration,
      photo_asset_id: sc.photo_id || null,
    })),
  };
}

/** Seconds a disclosure needs on screen (the backend's reading rule: 3 words a second, at least 2 s). */
export function disclosureSeconds(text: string): number {
  return Math.max(2, text.trim().split(/\s+/).filter(Boolean).length / 3);
}

/** Checks a file before uploading it as a reference image. Returns an error message or null. */
export function checkReferenceFile(file: { type: string; size: number }, existing: number): string | null {
  if (existing >= MAX_REFERENCES) return `A brief can cite at most ${MAX_REFERENCES} reference images.`;
  if (file.type !== "image/png" && file.type !== "image/jpeg") return "Reference images must be PNG or JPEG.";
  if (file.size > MAX_REFERENCE_BYTES) return "Reference images are limited to 10 MB.";
  return null;
}
