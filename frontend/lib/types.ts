// Shapes returned by the Vids Production API (vids/docs/openapi.json and
// backend/src/vidsprod/api/schemas.py). Loose `Record<string, unknown>` is used where the
// backend itself returns free-form dicts.

export type Json = Record<string, unknown>;

export const STAGE_ORDER = [
  "INTAKE",
  "CREATIVE",
  "SCRIPT",
  "SCENE_PLAN",
  "PROMPTS",
  "PLAN_APPROVAL",
  "VIDS_SESSION",
  "VIDS_PROJECT",
  "SCENE_PRODUCTION",
  "TIMELINE_ASSEMBLY",
  "TIMELINE_EDITS",
  "PREVIEW",
  "EXPORT",
  "FINAL_QA",
  "COMPLIANCE_APPROVAL",
  "DELIVERY",
] as const;
export type StageId = (typeof STAGE_ORDER)[number];

export type StageStatus =
  | "PENDING"
  | "RUNNING"
  | "VALIDATING"
  | "PASSED"
  | "PASSED_WITH_WARNINGS"
  | "FAILED"
  | "RETRYING"
  | "WAITING_FOR_HUMAN"
  | "SKIPPED"
  | "CANCELLED";

export type JobStatus =
  | "QUEUED"
  | "RUNNING"
  | "PAUSED"
  | "WAITING_FOR_HUMAN"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export type SceneStatus =
  | "PLANNED"
  | "READY"
  | "SUBMITTED"
  | "GENERATING"
  | "GENERATED"
  | "INSPECTING"
  | "ACCEPTED"
  | "NEEDS_EDIT"
  | "REGENERATION_REQUIRED"
  | "HUMAN_REVIEW"
  | "BLOCKED"
  | "FAILED";

export type ApprovalKind = "PLAN" | "SCENE_REVIEW" | "COMPLIANCE" | "SUBMISSION" | "INTERVENTION";
export type Severity = "BLOCKER" | "MAJOR" | "MINOR" | "INFO";
export type Outcome = "PASS" | "FAIL" | "NOT_RUN";
export type Category = "OPERATIONAL" | "CONTENT" | "BRAND_POLICY" | "TECHNICAL";

export interface ErrorInfo {
  code?: string;
  message?: string;
  details?: Json;
  [key: string]: unknown;
}

export interface JobSummary {
  id: string;
  campaign_id: string;
  campaign_name: string | null;
  status: JobStatus;
  control: string | null;
  current_stage: StageId | null;
  simulation: boolean;
  adapter: string;
  ai_provider: string;
  progress: number;
  stages_passed: number;
  stages_total: number;
  error: ErrorInfo | null;
  created_by: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface SceneCounts {
  [status: string]: number;
}

export interface StageOut {
  stage: StageId;
  status: StageStatus;
  attempts: number;
  attempt_id: string | null;
  next_permitted_action: string | null;
  error: ErrorInfo | null;
  progress: (Json & { scene_total?: number; active_scene?: string | null; scene_counts?: SceneCounts }) | null;
  started_at: string | null;
  finished_at: string | null;
  failures: number;
}

export interface Approval {
  id: string;
  job_id: string;
  kind: ApprovalKind;
  subject: string;
  status: "PENDING" | "APPROVED" | "REJECTED" | "CANCELLED" | "INVALIDATED";
  title: string;
  summary: string;
  payload: Json;
  options: string[];
  decision: Json | null;
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
}

export interface Budget {
  period: string;
  monthly_limit: number;
  monthly_used: number;
  campaign_used?: number;
  campaign_limit?: number;
}

export interface VidsProject {
  url?: string;
  title?: string;
  simulated?: boolean;
  project_id?: string;
  created_by_us?: boolean;
  [key: string]: unknown;
}

export interface JobDetail extends JobSummary {
  stages: StageOut[];
  approvals: Approval[];
  budget: Budget;
  active_scene: string | null;
  current_action: string | null;
  vids_project: VidsProject | null;
}

export interface Validation {
  id?: string;
  rule_id: string;
  category: Category;
  severity: Severity;
  outcome: Outcome;
  message: string;
  judge: string;
  subject: string | null;
  evidence: string[];
  fix_hint: string | null;
  data: Json;
  created_at?: string;
}

export interface StageAttempt {
  id: string;
  attempt: number;
  status: StageStatus;
  next_permitted_action: string | null;
  output: Json | null;
  error: ErrorInfo | null;
  progress: Json | null;
  input_hash: string | null;
  started_at: string | null;
  validated_at: string | null;
  finished_at: string | null;
  validations: Validation[];
}

export interface Take {
  id: string;
  take_no: number;
  origin: "GENERATE" | "EDIT";
  status: string;
  verdict: string | null;
  verdict_reasons: string[];
  correction: string | null;
  edit_instruction: string | null;
  evidence: string[];
  locked: boolean;
  simulated: boolean;
  prompt_text: string;
  created_at: string;
  validations: Validation[];
}

export interface SceneVersion {
  id: string;
  version: number;
  reason: string;
  spec: Json;
  prompt: Json;
  created_at: string;
}

export interface Scene {
  id: string;
  scene_key: string;
  order_index: number;
  status: SceneStatus;
  takes_used: number;
  edits_used: number;
  extra_takes_granted: number;
  locked_take_id: string | null;
  pending_instruction: string | null;
  current_version: SceneVersion | null;
  versions: SceneVersion[];
  takes: Take[];
}

export interface Artifact {
  id: string;
  job_id: string | null;
  kind: string;
  mime: string;
  size_bytes: number;
  sha256: string;
  producer: string;
  simulated: boolean;
  meta: Json;
  created_at: string;
}

export interface AuditEvent {
  id: number;
  job_id: string | null;
  campaign_id: string | null;
  actor: string;
  type: string;
  message: string;
  data: Json;
  created_at: string;
}

export interface ExportOut {
  id: string;
  attempt: number;
  status: string;
  artifact_id: string | null;
  format: string;
  duration_seconds: number | null;
  size_bytes: number | null;
  probe: Json | null;
  verification: Validation[];
  simulated: boolean;
  created_at: string;
}

export interface DeliveryOut {
  id: string;
  export_id: string;
  status: string;
  target: string;
  location: string | null;
  sha256: string;
  simulated: boolean;
  note: string | null;
  created_at: string;
}

export interface AIRequest {
  id: string;
  purpose: string;
  provider: string;
  model: string;
  status: string;
  attempts: number;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_estimate: number | null;
  error: string | null;
  created_at: string;
}

export interface ApprovedFact {
  fact_id: string;
  text: string;
  source: string;
}

export interface BrandProfile {
  id: string;
  name: string;
  tone: string;
  regulated: boolean;
  design_digest: string | null;
  palette: string[];
  forbidden_colours: string[];
  forbidden_terms: string[];
  approved_facts: ApprovedFact[];
  standard_disclosures: string[];
  logo_artifact_id: string | null;
  logo_position: string;
  updated_at: string;
}

export interface ClaimInput {
  text: string;
  fact_id?: string | null;
}

export interface DisclosureInput {
  text: string;
  on_screen: boolean;
  spoken: boolean;
}

export interface OutputRequirements {
  aspect_ratio: "16:9" | "9:16";
  min_duration_seconds?: number | null;
  max_duration_seconds?: number | null;
  language: string;
  captions_required: boolean;
  voiceover_required: boolean;
  end_card_required: boolean;
  /** Written scenes only: false puts no text on the video. */
  on_screen_text?: boolean;
}

export interface BriefInput {
  raw_brief: string;
  objective?: string | null;
  audience?: string | null;
  product?: string | null;
  key_message?: string | null;
  cta?: string | null;
  tone?: string | null;
  required_claims: ClaimInput[];
  disclosures: DisclosureInput[];
  mandatory_elements: string[];
  reference_asset_ids: string[];
  output: OutputRequirements;
  /** Scenes written by the person; when present they replace AI planning. */
  scenes?: UserScene[];
  /** An uploaded logo, placed top right on every written scene. */
  logo_asset_id?: string | null;
}

export interface UserScene {
  prompt: string;
  on_screen_text?: string | null;
  duration_seconds: number;
  photo_asset_id?: string | null;
}

export interface Campaign {
  id: string;
  name: string;
  status: string;
  owner: string;
  brand_profile_id: string;
  current_brief_version: number;
  row_version: number;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  brief: (Partial<BriefInput> & Json) | null;
  latest_job: JobSummary | null;
}

export interface BriefVersion {
  id: string;
  version: number;
  content: Partial<BriefInput> & Json;
  created_by: string;
  created_at: string;
}

export interface Capability {
  key: string;
  description: string;
  status: "VERIFIED" | "UNVERIFIED" | "UNSUPPORTED" | "REQUIRES_HUMAN";
  source: "documented" | "observed" | "simulated" | "unknown";
  reference: string | null;
  params: Json;
  verified_on: string | null;
  ui_fingerprint: string | null;
  evidence: string[];
  notes: string | null;
}

export interface SystemInfo {
  settings: Json & {
    app_env: string;
    ai_provider: string;
    google_vids_mode: "mock" | "browser_test" | "live";
    live_actions_allowed: boolean;
    simulation: boolean;
  };
  simulation: boolean;
  provider_error: string | null;
  capabilities: Capability[];
  capabilities_simulated: boolean;
  budget: Budget;
  brand: { available: boolean; digest: string | null; palette: string[] };
}

export interface SessionCheck {
  state: string;
  authenticated: boolean;
  account: string | null;
  message: string | null;
  page: string | null;
  checked_at?: string;
  evidence_artifact_id: string | null;
  account_confirmed?: boolean;
  ui_fingerprint_matches?: boolean | null;
  resumed_jobs?: string[];
}

export interface Readiness {
  state: "MOCK" | "NOT_CHECKED" | "SIGNED_OUT" | "RECON_INCOMPLETE" | "LIVE_BLOCKED" | "LIVE_READY";
  label: string;
  steps: { key: string; label: string; ok: boolean }[];
  blockers: string[];
  verified?: string[];
  missing?: string[];
}

export interface BrowserStatus {
  mode: "mock" | "browser_test" | "live";
  live_actions_allowed: boolean;
  live_automation_flag: boolean;
  allowed_account_configured: boolean;
  simulated: boolean;
  last_check: SessionCheck | null;
  readiness?: Readiness;
  /** Set while a generated clip is in the open page but not proven inserted (closing or reloading loses it). */
  unsaved_clip?: { project: string; scene_key: string; take_no: number; label: string } | null;
  browser: Json & { enabled?: boolean; running?: boolean; mode?: string; page?: string | null; busy?: boolean; last_error?: string | null };
}

export interface PlanDoc<T = Json> {
  version: number;
  content: T;
}

export interface Plan {
  CAMPAIGN_BRIEF: PlanDoc | null;
  CREATIVE_PLAN: PlanDoc | null;
  SCRIPT: PlanDoc | null;
  SCENE_PLAN: PlanDoc | null;
  PROMPTS: PlanDoc | null;
}

export interface QaDocument {
  kind: string;
  version: number;
  content: Json & {
    verdict?: string;
    summary?: string;
    sections?: Record<string, Validation[]>;
    actionable_issues?: Json[];
    export?: Json;
    export_artifact_id?: string;
    frame_artifact_ids?: string[];
    simulated?: boolean;
  };
  content_hash: string;
  created_at: string;
}
