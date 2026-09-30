// One mapping from every backend status vocabulary to a visual tone and a plain-language
// label. Status is always shown as icon + text, never by colour alone.

export type Tone =
  | "done" // finished successfully
  | "warn" // finished, with warnings
  | "live" // in progress
  | "attention" // waiting on a person
  | "fail" // failed or blocked
  | "idle" // not started
  | "muted"; // skipped, cancelled, not run

export interface StatusMeta {
  label: string;
  tone: Tone;
}

const STAGE: Record<string, StatusMeta> = {
  PENDING: { label: "Pending", tone: "idle" },
  RUNNING: { label: "Running", tone: "live" },
  VALIDATING: { label: "Validating", tone: "live" },
  PASSED: { label: "Passed", tone: "done" },
  PASSED_WITH_WARNINGS: { label: "Passed with warnings", tone: "warn" },
  FAILED: { label: "Failed", tone: "fail" },
  RETRYING: { label: "Retrying", tone: "live" },
  WAITING_FOR_HUMAN: { label: "Waiting for a person", tone: "attention" },
  SKIPPED: { label: "Skipped", tone: "muted" },
  CANCELLED: { label: "Cancelled", tone: "muted" },
};

const JOB: Record<string, StatusMeta> = {
  QUEUED: { label: "Queued", tone: "idle" },
  RUNNING: { label: "Running", tone: "live" },
  PAUSED: { label: "Paused", tone: "attention" },
  WAITING_FOR_HUMAN: { label: "Waiting for a person", tone: "attention" },
  COMPLETED: { label: "Completed", tone: "done" },
  FAILED: { label: "Failed", tone: "fail" },
  CANCELLED: { label: "Cancelled", tone: "muted" },
};

const SCENE: Record<string, StatusMeta> = {
  PLANNED: { label: "Planned", tone: "idle" },
  READY: { label: "Ready", tone: "idle" },
  SUBMITTED: { label: "Submitted", tone: "live" },
  GENERATING: { label: "Generating", tone: "live" },
  GENERATED: { label: "Generated", tone: "live" },
  INSPECTING: { label: "Inspecting", tone: "live" },
  ACCEPTED: { label: "Accepted", tone: "done" },
  NEEDS_EDIT: { label: "Needs edit", tone: "warn" },
  REGENERATION_REQUIRED: { label: "Regeneration required", tone: "warn" },
  HUMAN_REVIEW: { label: "Needs review", tone: "attention" },
  BLOCKED: { label: "Blocked", tone: "fail" },
  FAILED: { label: "Failed", tone: "fail" },
};

const APPROVAL: Record<string, StatusMeta> = {
  PENDING: { label: "Pending decision", tone: "attention" },
  APPROVED: { label: "Approved", tone: "done" },
  REJECTED: { label: "Rejected", tone: "fail" },
  CANCELLED: { label: "Cancelled", tone: "muted" },
  INVALIDATED: { label: "Invalidated", tone: "muted" },
};

const OUTCOME: Record<string, StatusMeta> = {
  PASS: { label: "Pass", tone: "done" },
  FAIL: { label: "Fail", tone: "fail" },
  NOT_RUN: { label: "Not run", tone: "muted" },
};

const VERDICT: Record<string, StatusMeta> = {
  PASS: { label: "Pass", tone: "done" },
  PASS_WITH_WARNINGS: { label: "Pass with warnings", tone: "warn" },
  NEEDS_EDIT: { label: "Needs edit", tone: "warn" },
  NEEDS_REGENERATION: { label: "Needs regeneration", tone: "warn" },
  HUMAN_REVIEW: { label: "Needs review", tone: "attention" },
  FAIL: { label: "Fail", tone: "fail" },
};

const CAPABILITY: Record<string, StatusMeta> = {
  VERIFIED: { label: "Verified", tone: "done" },
  UNVERIFIED: { label: "Unverified", tone: "warn" },
  UNSUPPORTED: { label: "Unsupported", tone: "muted" },
  REQUIRES_HUMAN: { label: "Requires a person", tone: "attention" },
};

const SESSION: Record<string, StatusMeta> = {
  READY: { label: "Ready", tone: "done" },
  SIGNED_IN: { label: "Signed in", tone: "done" },
  ACCOUNT_UNCONFIRMED: { label: "Account unconfirmed", tone: "warn" },
  SIGNED_OUT: { label: "Signed out", tone: "attention" },
  WRONG_ACCOUNT: { label: "Wrong account", tone: "fail" },
  UI_CHANGED: { label: "Vids interface changed", tone: "warn" },
  CHALLENGE: { label: "Google security check", tone: "attention" },
  DISABLED: { label: "Disabled", tone: "muted" },
  NOT_RUNNING: { label: "Chrome not running", tone: "idle" },
  ERROR: { label: "Error", tone: "fail" },
  SIMULATED: { label: "Simulated (no real session)", tone: "muted" },
};

const EXPORT: Record<string, StatusMeta> = {
  REQUESTED: { label: "Requested", tone: "live" },
  DOWNLOADED: { label: "Downloaded", tone: "live" },
  VERIFIED: { label: "Verified", tone: "done" },
  FAILED: { label: "Failed", tone: "fail" },
  STORED: { label: "Stored", tone: "done" },
  SUBMITTED: { label: "Submitted", tone: "done" },
  BLOCKED: { label: "Blocked", tone: "fail" },
};

const SEVERITY: Record<string, StatusMeta> = {
  BLOCKER: { label: "Blocker", tone: "fail" },
  MAJOR: { label: "Major", tone: "warn" },
  MINOR: { label: "Minor", tone: "idle" },
  INFO: { label: "Info", tone: "muted" },
};

export type StatusKind =
  | "stage"
  | "job"
  | "scene"
  | "approval"
  | "outcome"
  | "verdict"
  | "capability"
  | "session"
  | "export"
  | "severity";

const TABLES: Record<StatusKind, Record<string, StatusMeta>> = {
  stage: STAGE,
  job: JOB,
  scene: SCENE,
  approval: APPROVAL,
  outcome: OUTCOME,
  verdict: VERDICT,
  capability: CAPABILITY,
  session: SESSION,
  export: EXPORT,
  severity: SEVERITY,
};

export function humanise(value: string): string {
  const s = value.replace(/_/g, " ").toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Unknown values fall back to a neutral tone and a humanised label, never to "success". */
export function statusMeta(kind: StatusKind, value: string | null | undefined): StatusMeta {
  if (!value) return { label: "Unknown", tone: "idle" };
  return TABLES[kind][value] ?? { label: humanise(value), tone: "idle" };
}

export const ACTIVE_JOB_STATUSES = new Set(["QUEUED", "RUNNING", "PAUSED", "WAITING_FOR_HUMAN"]);
export const TERMINAL_JOB_STATUSES = new Set(["COMPLETED", "CANCELLED"]);

export const STAGE_LABELS: Record<string, string> = {
  INTAKE: "Brief intake",
  CREATIVE: "Creative plan",
  SCRIPT: "Script",
  SCENE_PLAN: "Scene plan",
  PROMPTS: "Prompts",
  PLAN_APPROVAL: "Plan approval",
  VIDS_SESSION: "Vids session",
  VIDS_PROJECT: "Vids project",
  SCENE_PRODUCTION: "Scene production",
  TIMELINE_ASSEMBLY: "Timeline assembly",
  TIMELINE_EDITS: "Timeline edits",
  PREVIEW: "Preview",
  EXPORT: "Export",
  FINAL_QA: "Final QA",
  COMPLIANCE_APPROVAL: "Compliance approval",
  DELIVERY: "Delivery",
};

export const CATEGORY_LABELS: Record<string, string> = {
  OPERATIONAL: "Operational",
  CONTENT: "Content",
  BRAND_POLICY: "Brand and policy",
  TECHNICAL: "Technical",
};

export const APPROVAL_KIND_LABELS: Record<string, string> = {
  PLAN: "Plan approval",
  SCENE_REVIEW: "Scene review",
  COMPLIANCE: "Compliance approval",
  SUBMISSION: "Submission approval",
  INTERVENTION: "Intervention",
};
