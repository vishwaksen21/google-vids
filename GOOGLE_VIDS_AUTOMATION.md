# Google Vids Automation

**Status (2026-09-29):**

| Part | Status |
|---|---|
| Browser session manager, sign-in detection, manual-login flow, diagnostics, recovery mapping | Implemented. Tested against local fixture pages; **sign-in detection tested once against the real site** (signed out → `SIGNED_OUT`, see §6) |
| Adapter recipes (create project, prompt, generate, insert, export) | Implemented against Google's documented flow. **Not runnable yet**: no Vids locator has been recorded by recon, so every live capability is `UNVERIFIED` and refuses to run |
| Mock adapter | Implemented and tested: the whole pipeline runs on it, on real media files |
| Live generation, editing, timeline and export in the real Vids editor | **Not integration-tested.** Blocked on (1) you signing in, (2) recon, (3) you enabling live automation |

## 1. Why a browser, and the three modes

There is no public Google Vids API (checked 2026-09-29), so the agent operates the Vids web
editor through Playwright on a dedicated Chrome profile.

| Mode (`GOOGLE_VIDS_MODE`) | What happens | Spends quota |
|---|---|---|
| `mock` (default) | Simulated Vids. The mock renders real MP4s with ffmpeg and can inject defects, errors, lost edits, truncated exports, expired sessions and crashes | no |
| `browser_test` | Real Chrome, real `vids.google.com`, **read-only**: launch, sign-in detection, manual login, diagnostics. Every action that would create, submit, edit or export is refused. A production job run in this mode stops at the session stage with the session result | no |
| `live` | Real project creation, generation, editing and export, and only when `GOOGLE_VIDS_LIVE_AUTOMATION_ENABLED=true` and each capability is verified | yes |

## 2. What Google documents

Evidence level: documented by Google, **not yet observed** by us. The planner uses the
documented clip limits and flags them as unverified in every plan
(`plan.clip_limits_source`, MINOR).

| Capability | Documented | Source |
|---|---|---|
| Generate a clip | "Create AI videos" → "Create" → prompt → "Generate"; "Insert" adds it to the Vid; "Recreate" reuses the prompt | [Help: generate video clips](https://support.google.com/docs/answer/16143507) |
| Clip duration | 3–10 seconds | same |
| Aspect ratio | Landscape 16:9 or Portrait 9:16 | same |
| Reference images | "Ingredients", up to 7 images | same |
| Quota | about 50 generations per user per month, reset 00:00 Pacific on the 1st | same |
| Model and editing | Gemini Omni Flash, 1080p; text-instruction editing of clips | [Workspace Updates, July 2026](https://workspaceupdates.googleblog.com/2026/07/generate-higher-quality-ai-video-clips-and-edit-any-video-with-Gemini-Omni-in-Vids.html) |
| Extend | extend an existing clip; several generations at once | [Workspace Updates, June 2026](https://workspaceupdates.googleblog.com/2026/06/create-longer-veo-videos-and-generate-multiple-at-once-in-Google-Vids.html) |
| Editor | scenes, timeline, transitions, text, images, stock media, audio, voice-over, templates | [Help: get started](https://support.google.com/docs/answer/15082958) |
| Export | File → "Download as MP4"; "Export to Drive" | same |

## 3. Open questions for recon

1. Prompt box character limit (`clip.generate.params.max_prompt_chars`).
2. Is duration a whole-second control?
3. Do Recreate, Extend or an Omni edit cost a generation? (The ledger assumes an edit does.)
4. How is *our* generated clip identified (order, label, card)?
5. How can a clip be captured for inspection: a preview `<video>` element, scrubbing, or card
   screenshots only? Until verified, the browser adapter returns a screenshot of the result card
   and scene QA defers frame/audio analysis to the export (recorded as `NOT_RUN`, not passed).
6. Does Omni generate speech? Until verified, scripts are voiceover-first and prompts say
   nobody speaks on camera.
7. How transitions, text, the logo image, AI voice-over and music are applied and read back.
8. How export completion is signalled and where the file lands.
9. What the failure states look like: refusal, quota, error, expired session.
10. Stability of roles and accessible names (the fingerprint landmarks).

## 4. Implementation

`backend/src/vidsprod/automation/google_vids/`:

| Module | Role |
|---|---|
| `browser_manager.py` | One persistent Chrome context on `BROWSER_PROFILE_DIR` (0700). Lazy launch, **relaunch after a crash**, `lock` serialising every browser operation, status for the dashboard, URLs reported without query strings |
| `authentication.py` | Sign-in detection without Vids-specific selectors: redirect to `accounts.google.com` → `SIGNED_OUT`; verification/"unusual traffic" → `CHALLENGE` (stop); on the Vids host, the allowed account's address present in the accessibility tree → `SIGNED_IN`, another address → `WRONG_ACCOUNT`, none → `ACCOUNT_UNCONFIRMED`. `open_login` / `wait_for_login` for the manual flow |
| `selectors.py` | The locator catalogue (loaded from `recon/locators.json`) and `PageDriver`: `expect_one` (exactly one match, else a UI mismatch with a screenshot), `click`, `fill_verified` (reads the value back), `select_verified`, `upload_verified`, `wait_for_count` (with failure landmarks), a UI fingerprint, and a trace of action / expected / observed for each step |
| `diagnostics.py` | Read-only selector discovery: screenshot, accessibility snapshot, every visible control by role and name, and candidate locators with an ambiguity flag |
| `recovery.py` | Maps each session state to the error the engine acts on: `SIGNED_OUT` → wait for sign-in, `WRONG_ACCOUNT`/`ACCOUNT_UNCONFIRMED` → wait, `CHALLENGE` → wait (never bypassed), `UI_CHANGED` → wait, `SIGNED_IN` without a verified UI → blocked, `browser_test` → blocked with the session result |
| `adapter.py` | The unified interface: `launch`, `authenticate`, `open_home`, `create_project`, `configure_project`, `enter_prompt`, `upload_reference`, `generate_scene`, `inspect_scene`, `edit_scene`, `regenerate_scene`, `assemble_timeline`, `export_video`, `verify_export`, `close`, plus the workflow port methods composed from them |

Every mutating step follows **EXECUTE → WAIT → INSPECT → VERIFY → UPDATE STATE**. The driver
performs the action, waits for the expected state, and the step raises if that state is not
observed. The workflow updates state only after the result is confirmed (through the intent
log, §5). Clicking an irreversible control (Generate, Insert, Download) marks the step so that
a later failure is reported as "outcome unknown" rather than "did not happen".

**Gates, in order, on every mutating call:** `live` mode *and* the authorisation flag, then the
capability must be `VERIFIED`, then every locator key it needs must be in the catalogue, then
the session must have confirmed the verified UI fingerprint.

**Reconciliation after a crash:** the browser adapter answers `UNKNOWN` until a reliable way to
match a past action to the editor is verified, so the job waits for a person to look at the Vid
and record "it happened" or "it did not". It never re-submits a generation on a guess.

## 5. Manual login and resume

1. The dashboard's **Login** page shows the last session check. **Open sign-in window**
   (`POST /api/browser/login`) opens Google Vids in the dedicated Chrome window, where Google
   redirects to its sign-in page.
2. The person signs in there by hand.
3. **I've signed in** (`POST /api/browser/login/complete`) re-checks the session. If it is
   signed in as the allowed account, every job waiting for sign-in (`SESSION_EXPIRED`,
   `WRONG_ACCOUNT`, `GOOGLE_CHALLENGE`) is approved to retry, and the queue continues.

From a terminal: `GOOGLE_VIDS_MODE=browser_test uv run vidsprod browser login` does the same
and waits up to 5 minutes. The profile keeps the session, so this is needed once, and again
only when Google expires it.

## 6. Evidence from the real site

On 2026-09-29, `GOOGLE_VIDS_MODE=browser_test vidsprod browser check` launched Chrome on the
new profile, opened `https://vids.google.com/`, was redirected to
`https://accounts.google.com/v3/signin/identifier` ("Sign in … to continue to Google Vids"),
and reported `SIGNED_OUT` with a screenshot. No input was typed and nothing was clicked. This
verifies signed-out detection only. Signed-in detection (the account address in the
accessibility tree) is a heuristic until observed after a real sign-in.

## 7. What remains for real integration

1. You sign in once in the dedicated profile (§5).
2. Recon records locators and the fingerprint (`recon/README.md`), answering §3.
3. Remaining recipes are completed where recon shows how the UI works: Omni edit, timeline
   read-back, each timeline operation, clip capture and reconciliation.
4. You confirm automation of the account is permitted and set `GOOGLE_VIDS_MODE=live`,
   `GOOGLE_VIDS_LIVE_AUTOMATION_ENABLED=true`.
5. One controlled campaign end to end, watched, with a small generation budget.
