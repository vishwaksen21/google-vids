# Testing

## Running the tests

```bash
cd backend
uv run pytest                              # all backend tests (needs Postgres; see below)
uv run pytest --cov=vidsprod               # with coverage
uv run pytest tests/unit                   # no database needed
uv run ruff check src tests                # lint
```

Database tests use `TEST_DATABASE_URL` from `backend/.env`. The suite migrates the test
database with Alembic once per session and truncates every table between tests. Nothing in the
suite contacts Google or an AI provider: it uses the mock Vids adapter, the fake AI provider,
`httpx.MockTransport` for the gateway contract, and local fixture pages for the browser adapter.
Browser tests need Playwright's Chromium once: `uv run playwright install chromium`.

Frontend: see `frontend/README.md` (typecheck, lint, build, unit tests, Playwright E2E against
a running backend in mock mode).

## Backend results (2026-09-29)

**204 passed, 0 failed**, backend line coverage **85%** (`uv run pytest --cov`). Ruff clean.

| Suite | Tests | Covers |
|---|---|---|
| `unit/test_brand_ported.py` | 32 | ported from media-generation: DESIGN.md parsing, per-stage brand slices, colour scope, alignment, style |
| `unit/test_dialogue_ported.py` | 45 | ported: exact-words dialogue contract, speaker/order/allocation, vocalisation guard, transcript comparison (fillers, noise, repeats), Indic word counting, speech sizing, duration balancing |
| `unit/test_dynamic_planning.py` | 13 | scene count and duration follow the script, speech rate, clip limits and brief bounds; splitting, stretching, refusal with arithmetic |
| `unit/test_policies.py` | 17 | gate decisions (no evidence ≠ pass), verdict derivation, the correction-policy matrix, bounded positive steering |
| `unit/test_ai_service.py` | 18 | structured output, repair round, bounded retries, timeouts, model selection, request tracking without content, gateway wire contract (header auth, config, inline media, thinking budget, error classes), direct Gemini off by default, request shape and `store=false`, schema conversion |
| `unit/test_config_logging_media.py` | 15 | unsafe configuration refused, live needs mode and flag, no secrets in the public view, log redaction, probing, corrupted file not playable, a file *named* "truncated" not judged by its name, black/freeze (including freeze-to-end)/silence detection |
| `workflow/test_happy_path.py` | 1 | a full 16-stage production on real media; every stage has validation results; nothing regenerated |
| `workflow/test_correction_and_recovery.py` | 9 | selective regeneration and edit; take limit, then human review while other scenes continue; human-requested take with instruction; **crash after generation → reconciled, not regenerated**; two workers never share a job; truncated export re-exported; lost timeline edit re-applied; budget exhaustion blocks and resumes; expired session waits for sign-in |
| `workflow/test_controls_and_gates.py` | 13 | pause at a checkpoint and resume; cancel keeps history; rejected plan; stale approval; retry adds attempts; planning cannot be redone after production; one active production; **locked takes and audit trail immutable under direct SQL**; approvals name the decider; stale lease reclaimed; a brief without a CTA still plans (regression); the conflict names the active production |
| `workflow/test_budget_and_intents.py` | 5 | Pacific monthly period; monthly and campaign limits; timeout reconciled as not-happened and released; refused prompt released and sent to review; unknown outcome waits and a person's answer settles the budget |
| `workflow/test_api.py` | 13 | auth, required actor, readiness, campaign CRUD with versions and optimistic concurrency, validation, upload content checks, archive rules, idempotent start, **a full production over HTTP**, controls, scene review, browser endpoints in mock mode, SSE event stream and resume cursor |
| `browser/test_browser_adapter.py` | 23 | real headless Chromium on synthetic fixtures: mock never launches; browser_test refuses every mutating action and clicks nothing; live still refuses unverified capabilities; recon cannot claim VERIFIED without evidence; sign-in states; wrong host = signed out; state → recovery mapping; verified clicks/fills with trace; truncating field caught; missing/ambiguous elements never guessed; full generation recipe on a harness; UI change demotes capabilities; diagnostics; crash relaunch; 0700 profile |

Bugs found by these tests and fixed: mock footage too static for freeze detection; decode
errors guessed from words in the output; a freeze lasting to the end of a file ignored; infinity
in analysis JSON; lazy-loading after flush in async API responses; test settings inheriting the
developer's `.env`; the CTA check failing on punctuation ("today." ≠ "today"), found by the frontend E2E run.

## Frontend results (2026-09-29)

| Check | Result |
|---|---|
| `npm run typecheck` | clean |
| `npm run lint` | clean |
| `npm test` (vitest) | 7 files, **45 passed** |
| `npm run build` | succeeded, 17 routes |
| `npx playwright test` (E2E against the running backend in mock mode) | **1 passed**: create campaign → start → approve plan → approve compliance → Completed, 16/16 stages, verified export, stored delivery, playable video; the API token does not appear in the page |
| Visual check (screenshots of dashboard, monitor, scene review, storyboard, QA, login, phone width) | brand palette and logo per DESIGN.md, simulation banner and live-disabled indicator visible, real evidence frames and clip render through the proxy, no console errors |

## Manual and live checks performed

| Check | Result |
|---|---|
| Full production over HTTP against the running server (mock, "realistic" scenario) | COMPLETED; S02 regenerated (black frames), S03 edited (minor), 3 scenes untouched; 7 generations; QA 16/16; 26.08 s export verified |
| `vidsprod browser check` in BROWSER_TEST against the real `vids.google.com` | Chrome launched on the 0700 profile, redirected to Google sign-in, reported `SIGNED_OUT` with a screenshot |
| A BROWSER_TEST production over HTTP against the real site | planning ran, plan approved; VIDS_SESSION found the profile signed out and paused with a sign-in intervention; "I've signed in" refused to resume while still signed out |

## Not tested (and why)

| Not tested | Why |
|---|---|
| Signed-in detection on the real Vids page | the profile has not been signed in (you sign in by hand) |
| Any real Vids action: create, prompt, generate, insert, edit, timeline, export | no verified locators (recon), and live automation is not authorised |
| The company AI gateway end to end | no credentials in this environment; the wire contract is tested with the gateway's documented shapes |
| Direct Gemini end to end | disabled pending data-handling approval |

## Integration test checklist (real Vids)

- [ ] Sign in once: `GOOGLE_VIDS_MODE=browser_test uv run vidsprod browser login` → `SIGNED_IN`
- [ ] `vidsprod browser check` → `SIGNED_IN` with the masked allowed account
- [ ] Recon on home, the editor and the generation panel (`vidsprod browser diagnose`); fill `recon/locators.json`
- [ ] Verify `project.create` on a `[vidsprod]` scratch Vid; record evidence in `recon/capabilities.json`
- [ ] Verify `clip.generate` once (one generation), `clip.capture_frames`, `clip.insert`
- [ ] Verify `timeline.read`, then each timeline operation the plan needs
- [ ] Verify `export.download_mp4`
- [ ] Set `GOOGLE_VIDS_MODE=live`, `GOOGLE_VIDS_LIVE_AUTOMATION_ENABLED=true`, `GENERATION_CAMPAIGN_LIMIT` small
- [ ] One watched campaign end to end; compare the QA report with a human review of the export
- [ ] Kill the server mid-generation once; confirm the job waits for you to reconcile instead of regenerating
