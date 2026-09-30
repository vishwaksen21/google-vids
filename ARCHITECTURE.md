# Architecture

Status: **implemented** (2026-09-29). What is simulated and what is verified against the real
Google Vids is stated in [GOOGLE_VIDS_AUTOMATION.md](GOOGLE_VIDS_AUTOMATION.md).

## 1. Shape of the system

```
Dashboard (Next.js) ──server-side proxy (adds API token)──► FastAPI  ──►  Postgres
                                                               │           (state, history, audit)
                                        embedded worker ◄──────┘
                                          │ claims jobs (FOR UPDATE SKIP LOCKED + lease)
                                          ▼
                              Workflow engine (16 stages, validation gates)
                                 │                │                 │
                          AI service         Vids adapter       Media toolkit
                  (gateway | gemini | fake)  (mock | browser)   (bundled ffmpeg)
                                                   │
                                     Chrome, dedicated profile → vids.google.com
```

One process runs the API and the worker, because a Chrome profile can be open in only one
process and every browser operation must be serialised on one session.

## 2. The pipeline

| # | Stage | Does | Deterministic checks (examples) |
|---|---|---|---|
| 1 | INTAKE | natural-language brief → structured `CampaignBrief` (LLM). Output requirements, claims and disclosures are copied, not model-authored | every claim cites an approved fact; forbidden terms; duration bounds; open questions surfaced |
| 2 | CREATIVE | concept, beats, cast and locations with fixed appearance | objective and CTA beats exist; ids unique; beats fit the duration; brand alignment (ported) |
| 3 | SCRIPT | voiceover (and dialogue only if speech is a verified capability), supers, CTA | claims cite facts and use only their figures; CTA delivered; disclosures verbatim; **the script must fit the duration** (runs the scene planner) |
| 4 | SCENE_PLAN | **dynamic** scene count and durations from speech/reading time per beat, clip limits and the brief's bounds; LLM designs each scene's picture | every line allocated exactly once (ported); speech fits each scene (ported); no speech or text in visuals; total within bounds |
| 5 | PROMPTS | deterministic prompt composition: continuity text verbatim, brand colour slice (ported), "no text/logos in frame", "nobody speaks" unless verified | continuity text present; no logo instruction; aspect ratio supported |
| 6 | PLAN_APPROVAL | human gate; approval pinned to the plan's content hashes | approval stale if anything changed |
| 7 | VIDS_SESSION | session check (sign-in, account, UI fingerprint) + capability preflight for this plan | every needed capability verified |
| 8 | VIDS_PROJECT | create `[vidsprod] …` project (intent log), then reopen to verify | project exists and reopens |
| 9 | SCENE_PRODUCTION | per scene: generate → inspect → accept+lock / edit / regenerate this scene only / human review | see §4 |
| 10 | TIMELINE_ASSEMBLY | read back, reorder if needed, verify locked clips | order, locked scenes unchanged, durations |
| 11 | TIMELINE_EDITS | transitions (edit grammar, ported), voiceover, supers, disclosures for their reading time, logo end card, captions. One op per step, verified by read-back, re-applied if lost | each op persisted; locked scenes unchanged after every op |
| 12 | PREVIEW | preview screenshots per scene | captured; locked scenes unchanged |
| 13 | EXPORT | download, then verify: exists, size, MP4/H.264, aspect, duration vs timeline, full decode, audio, checksum. A failed export is exported again | all of those |
| 14 | FINAL_QA | the stored export: integrity, decode, duration, black/freeze, loudness, order, supers spelled exactly, CTA, disclosures, logo window, captions, transcript vs script, LLM review of frames | QA report with actionable issues |
| 15 | COMPLIANCE_APPROVAL | human gate on the verified export and QA report (hash-pinned) | stale if the export changed |
| 16 | DELIVERY | store deliverable (checksum-verified); submission only with a SUBMISSION approval; simulated output is never submitted | checksum matches |

## 3. The workflow engine (`workflow/engine.py`)

- **State lives in Postgres.** `production_job` holds the lease and status; each attempt at each
  stage is a `workflow_stage` row; results are `validation_result` rows. Nothing is overwritten:
  a retry is a new attempt.
- **One step = one handler call**, with no transaction open while the handler works. Pause,
  cancel and approval decisions take effect at the next checkpoint. Long stages (scene
  production, timeline edits) advance one unit per step.
- **Gate** (`workflow/gates.py`): no results → FAILED; only MINOR/INFO → PASSED_WITH_WARNINGS;
  MAJOR → automatic retry (bounded, exponential backoff) where the stage allows it, then a
  person; BLOCKER → a person. Earlier attempts' failures are fed back to planning agents as
  positive instructions.
- **Recovery by error type:** transient (timeout, disconnect, provider 5xx) → retry with backoff;
  human-required (signed out, wrong account, challenge, UI changed, budget or quota exhausted,
  unknown outcome) → an INTERVENTION approval, then retry; blocked (live disabled, capability
  unverified) → the job fails with the reason; anything else → the job fails with a traceback
  kept for engineers.
- **Leases:** `FOR UPDATE SKIP LOCKED` claim, heartbeat renewal, reclaim after expiry. A crashed
  worker's job resumes on the next worker from its last persisted state.
- **Intent log** (`workflow/intents.py`): every non-idempotent Vids action reserves budget and
  writes a PENDING intent first, then acts, then confirms the intent **and** writes its domain
  rows in one transaction. On resume, a PENDING intent is reconciled with the adapter (found /
  not found / unknown) before anything is attempted again.
- **Budget** (`workflow/budget.py`): reservations against a per-account monthly limit (Pacific
  reset) and a per-campaign limit, under a row lock. Spent when it happened, released only when
  it verifiably did not.

## 4. Scene production and correction

Scene states: PLANNED → READY → SUBMITTED → (GENERATING) → GENERATED → INSPECTING →
ACCEPTED (locked) | NEEDS_EDIT | REGENERATION_REQUIRED | HUMAN_REVIEW | BLOCKED | FAILED.

- **Inspection** (`agents/inspection.py`): deterministic media checks (evidence, duration, aspect,
  decode, black, freeze), a transcript compared with the approved words (ported dialogue
  contract), and an LLM vision judge with frames of the scene and of the accepted scenes it
  must stay continuous with. The verdict is **derived by rule** from the findings.
- **Correction policy** (pure function): PASS → accept and lock; minor or correctable → edit
  (if editing is verified and edits remain); otherwise → regenerate **this scene only**, with a
  new scene version whose prompt is steered by the findings (positive instructions, at most 3,
  never quoting the rejected take); limits reached or no budget → human review. People can
  accept, regenerate with an instruction, edit, or reject.
- A scene waiting for a person does not stop the others. Locked takes are immutable (database
  trigger) and are re-verified after every timeline edit.

## 5. Components

```
backend/src/vidsprod/
  api/           FastAPI routes (campaigns, jobs, approvals, artifacts, browser, SSE), auth
  agents/        planning.py (5 planning agents), inspection.py, timeline.py, final_qa.py
  automation/google_vids/   browser adapter (see GOOGLE_VIDS_AUTOMATION.md)
  brand/         DESIGN.md parser, per-stage brand slices, alignment (ported) + seed profile
  db/            SQLAlchemy models, UUIDv7 ids, sessions
  domain/        enums, planning documents, validation types
  llm/           AI service (timeouts, retries, schema repair, tracking) + providers
  media/         ffmpeg probe, full-decode analysis, frames, audio, fingerprints
  planning/      dialogue contract + dynamic duration planner (ported and new)
  storage/       artifact store
  vids/          adapter port, capability registry, mock adapter, factory
  workflow/      engine, gates, intents, budget, control, stage handlers
  cli.py         vidsprod serve | worker | migrate | doctor | browser | recon
frontend/        Next.js dashboard (see frontend/README.md)
recon/           locator catalogue and capability evidence (to be produced)
```

## 6. Data model

21 tables. Main ones: `campaign`, `campaign_brief` (versioned), `brand_profile`,
`production_job`, `workflow_stage` (attempts), `validation_result`, `job_document` (versioned
CAMPAIGN_BRIEF / CREATIVE_PLAN / SCRIPT / SCENE_PLAN / PROMPTS / TIMELINE_PLAN /
TIMELINE_STATE / QA_REPORT), `scene`, `scene_version`, `scene_take`, `timeline_op`,
`action_intent`, `budget_account`, `budget_entry`, `approval_request`, `browser_session`,
`artifact`, `export`, `delivery`, `audit_event`, `ai_request`. Migrations: Alembic
(`backend/alembic/versions/0001_initial_schema.py`), including the triggers for locked takes
and the append-only audit trail.

## 7. Decisions

| Decision | Why |
|---|---|
| Postgres state machine; no LangGraph, Temporal or Redis | serial browser work; one store for state, audit, leases, idempotency |
| SQLAlchemy 2 async rather than SQLModel | `SKIP LOCKED`, partial unique indexes, triggers |
| Deterministic prompt composition | continuity text must be verbatim; a model paraphrasing it breaks identity between scenes |
| Verdicts and corrections by rule, LLM as a judge only | reproducible, auditable decisions |
| Mock renders real media | every QA check runs on actual files, so simulated runs test the real validators |
| Company gateway default; direct Gemini off | data handling approval |

## 8. Reused from `phi-service-layer/media-generation`

Ported with their tests: the brand module (DESIGN.md parser, projections, alignment, style:
32 tests), the dialogue contract and speech sizing (exact words, speaker, order, one scene per
line, vocalisation guard, transcript comparison, script-aware word counting, duration balancing: 45 tests), the
duration balancer and dialogue-to-duration matcher, the retry-steering pattern, the edit grammar
(dissolve on a change of place), the verified gateway contract and the approved-fact catalogue
and compliance guardrails. **Not ported:** the FFmpeg composition pipeline and Veo-specific
logic; Vids renders, edits and exports.
