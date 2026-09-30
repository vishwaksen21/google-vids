# Validation Framework

Status: **implemented and tested** on the mock adapter and fake provider. Checks that need the
real Vids editor (clip capture, timeline read-back) are implemented but not integration-tested.

Every operation runs **EXECUTE → VERIFY → VALIDATE → DECIDE → PROCEED**:
- **Verify** checks that the operation happened, by reading back from Vids, the file system or
  the database, never from the return value of a click.
- **Validate** checks that the result is right.
- **Decide** is a rule, not a judgement call.

## 1. Records

| Record | Where | Holds |
|---|---|---|
| Stage attempt | `workflow_stage` | stage, attempt, status, output, progress, error, `next_permitted_action`, timestamps |
| Rule result | `validation_result` | `rule_id`, category (OPERATIONAL, CONTENT, BRAND_POLICY, TECHNICAL), severity (BLOCKER, MAJOR, MINOR, INFO), outcome (PASS, FAIL, NOT_RUN), message, judge (`deterministic`, `llm`, `human`), evidence artifact ids, fix hint, data. Linked to the stage attempt and, for scenes, to the take |
| Evidence | `artifact` | screenshots, frames, clips, audio, exports, diagnostics, with SHA-256 |
| Decisions | `approval_request`, `audit_event` | who decided what, why, about which hashed content |

Stage statuses: PENDING, RUNNING, VALIDATING, PASSED, PASSED_WITH_WARNINGS, FAILED, RETRYING,
WAITING_FOR_HUMAN, SKIPPED, CANCELLED.

## 2. The gate (`workflow/gates.py`)

| Results | Status | Next |
|---|---|---|
| none | FAILED | a person (no evidence is not a pass) |
| all PASS | PASSED | proceed |
| only MINOR/INFO failures | PASSED_WITH_WARNINGS | proceed; warnings stay visible |
| any MAJOR | FAILED | automatic retry with backoff (`STAGE_MAX_ATTEMPTS`), earlier failures fed back as instructions; then a person. Final QA does not retry automatically |
| any BLOCKER | FAILED | a person (INTERVENTION approval: retry or fail) |

NOT_RUN counts as a failure at its severity. A check that runs later (for example, clip audio
unavailable from Vids, so speech is checked on the export) is recorded as NOT_RUN at MINOR, so
it is visible, and it is not treated as a defect in the scene.

## 3. Rule catalogue (by prefix)

| Prefix | Checks |
|---|---|
| `intake.*` | claims cite approved facts (BLOCKER), figures come from the cited fact, forbidden language, duration bounds, open questions |
| `creative.*` | beat structure, objective/CTA beats, ids, fits the duration, brand alignment (ported) |
| `script.*` | speakers, dialogue only if speech is verified, claims and figures, supers as claims, CTA delivered, disclosures verbatim (BLOCKER), fits the duration |
| `plan.*` | scene keys, references, no speech or text in visuals, speech fits each scene, clip limits (BLOCKER), every line allocated exactly once (BLOCKER), total duration, source of the clip limits |
| `prompts.*` | continuity text verbatim, no logo instruction, prompt length (if known), aspect ratio |
| `approval.*` | plan/compliance approved by a named person over unchanged content |
| `session.*`, `capabilities.*`, `project.*` | session ready; every needed capability verified; project reopens |
| `tech.*` (per take) | evidence captured (BLOCKER if none), duration, aspect ratio, decode, black frames, freezes |
| `speech.*` (per take) | transcript vs approved words: nothing unapproved, nothing missing |
| `judge.*` (per take, LLM) | prompt adherence, brand, visual quality, continuity, text in frame, artifacts, narrative |
| `scene.*`, `scenes.*` | each scene locked with its generation and edit counts |
| `timeline.*` | order, locked scenes unchanged (BLOCKER), durations, each edit persisted by read-back |
| `export.*` | exists, size, format, aspect, duration vs timeline, full decode, audio, checksum |
| `qa.*` | integrity, decode, duration, black/freeze, audio, loudness, order, locked takes, transitions, supers spelled exactly, CTA, disclosures on screen long enough (BLOCKER), logo on the end card, captions, speech vs script, A/V proxy (stated as a proxy), LLM review of frames |
| `delivery.*` | stored copy matches the verified export; submission authorised |

## 4. Scene verdicts and correction

Verdicts are derived from the take's results (`agents/inspection.py: derive_verdict`):

- any BLOCKER → HUMAN_REVIEW
- MAJOR failures all correctable by an edit → NEEDS_EDIT, otherwise → NEEDS_REGENERATION
- MINOR failures that are correctable → NEEDS_EDIT, otherwise → PASS_WITH_WARNINGS
- none → PASS

The correction policy (`decide_correction`) is a pure function of the verdict, the scene's
takes and edits used, the limits, the budget and whether editing is a verified capability:

```
PASS / PASS_WITH_WARNINGS                               → ACCEPT_AND_LOCK
NEEDS_EDIT and edit verified and edits left and budget  → EDIT
(otherwise) takes left and budget                       → REGENERATE (this scene only)
otherwise                                               → HUMAN_REVIEW
```

Each regeneration is a new scene version with a steered prompt; each edit is a new take linked
to its parent. Earlier versions and takes, with their evidence, are kept.

## 5. Approvals

| Gate | When | Pinned to |
|---|---|---|
| PLAN | after planning, before anything is generated | content hashes of brief, creative plan, script, scene plan, prompts |
| SCENE_REVIEW | when inspection hands a scene to a person | the take and its evidence |
| COMPLIANCE | after final QA (always, for a regulated brand) | export SHA-256 and QA report hash |
| SUBMISSION | before delivery to a submission target | export id and SHA-256 |
| INTERVENTION | a stage needs a person (sign-in, budget, unknown outcome, blocking finding) | the error; an unknown-outcome intervention records whether the action happened |

A rejected gate fails that stage (BLOCKER), and the job waits for a person to retry or stop. An
approval whose pinned content changed no longer applies.

## 6. Tested behaviour

See [TESTING.md](TESTING.md). The workflow tests show:
- only the defective scene is regenerated, while accepted scenes keep one take each;
- a minor defect is edited, not regenerated;
- a persistent failure stops at the take limit and goes to a person while other scenes continue;
- a crash after a generation does not generate twice;
- a truncated export is caught and re-exported;
- a timeline edit that did not persist is detected and re-applied;
- an exhausted budget blocks the scene and resumes after a person acts;
- an expired session waits for sign-in, then continues;
- a changed plan invalidates its approval;
- locked takes and the audit trail cannot be altered, even with direct SQL.
