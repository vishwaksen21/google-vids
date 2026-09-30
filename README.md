# Vids Production

An autonomous video production agent for Prudential campaigns that uses **Google Vids** as its
creation and editing environment. You enter a brief. It plans the creative, operates Google Vids
through a real Chrome session you have signed in to, inspects every scene, fixes only what is
wrong, checks the finished film against the brief, brand and compliance rules, and delivers a
verified MP4, stopping for a person wherever a decision is theirs.

**Current state:** the whole system runs end to end in **MOCK** mode. **BROWSER_TEST** mode has
been run against the real Google Vids sign-in. **LIVE** generation in Vids is built but not yet
runnable: it needs you to sign in, a recon pass to record the Vids controls, and your
authorisation. See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for exact status and blockers.

## Run it

Prerequisites: PostgreSQL 16 running (`brew services start postgresql@16`), `uv`, Node 20+.
Local secrets are in `backend/.env` (see [.env.example](.env.example)).

```bash
# backend (API + worker), port 8100
cd backend
uv sync
uv run vidsprod migrate
uv run vidsprod doctor            # checks database, ffmpeg, DESIGN.md, provider, mode
uv run vidsprod serve             # http://localhost:8100/api/docs

# dashboard, port 3000
cd ../frontend
npm install
npm run dev                       # http://localhost:3000
                                  # (port taken? npm run dev -- --port 3100)
```

## Modes

| Mode | Set in `backend/.env` | What happens |
|---|---|---|
| **MOCK** | `GOOGLE_VIDS_MODE=mock` (default) | Simulated Vids that renders real video files, so every check runs for real. Scenarios: `MOCK_VIDS_SCENARIO=clean`, `realistic` (one regeneration, one edit), or `stress`. Everything is labelled SIMULATED |
| **BROWSER_TEST** | `GOOGLE_VIDS_MODE=browser_test` | Real Chrome and real Google Vids, **read-only**: launch, sign-in detection, manual login, diagnostics. No project, prompt, generation or export. Productions stop at the session stage and report what they found |
| **LIVE** | `GOOGLE_VIDS_MODE=live` **and** `GOOGLE_VIDS_LIVE_AUTOMATION_ENABLED=true` | Real project creation, generation, editing and export, for capabilities verified by recon. Spends Vids generation quota |

`GOOGLE_VIDS_ALLOWED_ACCOUNT` names the one account automation may operate (address only).

## Chrome and signing in (you do this, by hand)

The agent uses its own Chrome profile at `var/browser-profile` (owner-only permissions). It
**never asks for, types or stores your password**, and never reads cookies.

1. `cd backend && GOOGLE_VIDS_MODE=browser_test uv run vidsprod browser login`
   (or, with the server in browser_test/live mode, the dashboard's **Login** page → **Open sign-in window**).
2. A Chrome window opens on Google Vids and Google shows its sign-in page. Sign in there.
3. The command reports `SIGNED_IN` (or press **I've signed in** on the dashboard). Jobs that
   were waiting for sign-in continue by themselves.

The profile keeps the session, so you do this again only when Google expires it. If Google asks
for verification, complete it in that window; the agent stops and waits, and never attempts it.
Only one process can hold the profile at a time: use the CLI commands or the server, not both at once.

## Start a campaign

Dashboard → **New campaign** → write the brief in plain language (optionally: CTA, approved
claims, disclosures, duration, aspect ratio, reference images) → **Start production**.
The agent plans, then waits for **plan approval** (storyboard page). It produces the scenes,
edits, exports and checks, then waits for **compliance approval** (QA page), then delivers.
Watch it live on the production monitor, where you can pause, resume, cancel or retry.

## Enabling LIVE (only after authorisation)

1. Sign in (above) and complete recon ([recon/README.md](recon/README.md)).
2. Confirm that automating this account is permitted: Google's terms, and for a Workspace
   account the administrator's policy.
3. Set `GOOGLE_VIDS_MODE=live` and `GOOGLE_VIDS_LIVE_AUTOMATION_ENABLED=true`, keep
   `GENERATION_CAMPAIGN_LIMIT` small, restart, and run one watched campaign.

## Documents

| | |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | the pipeline, engine, correction, components, data model, what was reused |
| [GOOGLE_VIDS_AUTOMATION.md](GOOGLE_VIDS_AUTOMATION.md) | modes, the browser adapter, login flow, evidence from the real site, what remains |
| [VALIDATION_FRAMEWORK.md](VALIDATION_FRAMEWORK.md) | rules, gates, verdicts, correction policy, approvals |
| [SECURITY.md](SECURITY.md) | credentials, authorisation boundaries, API and data protection, compliance |
| [TESTING.md](TESTING.md) | how to run tests, actual results, integration checklist |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | status per phase, blockers |
| [recon/README.md](recon/README.md) | recording and verifying the Vids controls |
| [docs/DEVELOPMENT_SKILLS.md](docs/DEVELOPMENT_SKILLS.md) | Claude Code skills used |
| [frontend/README.md](frontend/README.md) | dashboard |
