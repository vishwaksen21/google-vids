# Implementation Plan and Status

Last updated 2026-09-29. Status labels:
- **Tested**: implemented, with tests passing.
- **Not integration-tested**: passes against fakes only.
- **Mocked**: only the mock adapter exists.
- **Unverified**: depends on a real Vids behaviour nobody has observed yet.
- **Blocked by authorisation**: waiting on a person's decision or sign-in.

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 1 Foundation | uv project, config with startup safety checks, JSON logging with redaction, errors, Postgres + Alembic (21 tables, triggers), health checks, `.env.example`, SECURITY.md, TESTING.md | **Tested** |
| 2 Database and state machine | persistent stages and attempts, leases, gates, retries with backoff, pause/resume/cancel/retry, approvals, audit trail, intent log, budget ledger | **Tested** (workflow suite on real Postgres) |
| 3 Browser session manager | dedicated 0700 profile, persistent context, serialised operations, crash relaunch, status | **Tested** on fixtures; launched against the real site once |
| 4 Vids navigation and authentication | sign-in detection, manual login and resume, challenge handling, account confirmation | Signed-out detection **tested against the real site**; signed-in detection **unverified** until you sign in |
| 5 Browser automation adapter | unified interface, locator catalogue, verified primitives, diagnostics, fingerprinting, mode and capability gates | Mechanics **tested** on a synthetic harness. Real Vids recipes **blocked**: no locator recorded yet (recon) |
| 6 Creative planning and AI | intake, creative, script, dynamic scene planner, prompt engineer; AI service with gateway / direct Gemini / fake | Agents and rules **tested** with the fake provider. Gateway **not integration-tested** (no credentials here; wire contract tested). Direct Gemini off by default, request shape tested |
| 7 Scene production | per-scene generation, inspection (media + transcript + LLM judge), correction policy, versions and takes, budget reservation, crash-safe intents | **Tested** on the mock adapter. **Unverified** on real Vids |
| 8 Validation and recovery | rule catalogue, verdicts, error-type recovery, reconciliation | **Tested** |
| 9 Timeline and export | edit plan, per-op read-back verification, locked-scene checks, export verification, final QA, delivery | **Tested** on the mock. **Unverified** on real Vids (timeline recipes need recon) |
| 10 Frontend | Next.js dashboard, 14 screens | See frontend/README.md |
| 11 End to end | full production over HTTP in mock mode | **Tested** (API test and live smoke run) |
| 12 Docs and hardening | docs; lint; 85% backend coverage | **Tested** |

## Blockers for real Google Vids production

| # | Blocker | Who | What unblocks it |
|---|---|---|---|
| B1 | The dedicated Chrome profile is not signed in | You | `GOOGLE_VIDS_MODE=browser_test uv run vidsprod browser login`, then sign in by hand |
| B2 | No Vids locator or capability is verified | Engineering with you watching | Recon (`recon/README.md`) on the signed-in profile |
| B3 | Live automation not authorised | You (account owner) | Confirm automating this account is allowed under Google's terms (and the Workspace admin's policy for a Workspace account), then set `GOOGLE_VIDS_MODE=live` and `GOOGLE_VIDS_LIVE_AUTOMATION_ENABLED=true` |
| B4 | Company AI gateway credentials | You / platform team | `GATEWAY_BASE_URL`, `GATEWAY_API_KEY`; run `vidsprod doctor` to confirm the configured models are offered |
| B5 | Recipes needing observed UI behaviour: Omni edit, timeline read-back and operations, clip capture, reconciliation | Engineering after B2 | Implement from recon evidence; each ships with a verification run |

## Risks carried forward

- **Quota:** about 50 generations per user per month. The ledger reserves before generating and
  stops at the campaign and monthly limits. A 30 s film with a few corrections uses about 8–10.
- **UI drift:** the fingerprint check demotes capabilities when the editor changes; production
  pauses instead of clicking blindly.
- **Speech in generated clips:** unknown until recon. Scripts are voiceover-first, and the
  transcript check catches invented speech wherever audio is obtainable.
- **Identity:** actors are declared, not authenticated. SSO is needed before multi-user use.
