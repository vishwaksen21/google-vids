# Vids Production dashboard

The operator dashboard for Vids Production: campaign briefs, live production monitoring,
plan, scene and compliance approvals, validation reports and delivery. It is a Next.js
(App Router) app styled strictly to [`../../DESIGN.md`](../../DESIGN.md) and talks only to the
FastAPI backend in `../backend`.

## Run it

```bash
npm install
cp .env.example .env.local   # then set API_TOKEN to the API_TOKEN in ../backend/.env
npm run dev                  # http://localhost:3000
```

The backend must be running (default `http://localhost:8100`, set `BACKEND_URL` to change it):

```bash
cd ../backend && uv run uvicorn vidsprod.main:app --port 8100
```

On first visit the dashboard asks for your name. It is sent as `X-Actor` with every change
and recorded in the audit trail; changes are blocked until it is set (Settings, or the prompt).

## How the backend is reached

The browser never sees `API_TOKEN`. Every request goes to the same-origin route
`/api/backend/<path>` (`app/api/backend/[...path]/route.ts`), which:

- forwards `GET/POST/PUT/PATCH` to `${BACKEND_URL}/api/<path>` with the query string and body;
- adds `Authorization: Bearer ${API_TOKEN}` on the server;
- passes through `X-Actor`, `Idempotency-Key`, `Content-Type`, `Accept` and `Range` only;
- streams bodies both ways, so multipart uploads, video seeking and the SSE stream
  (`/api/jobs/{id}/events`) work unbuffered;
- rejects anything that is not a plain path segment (`..`, empty or encoded separators) with 400.

Artifacts (frames, screenshots, clips, exports) are loaded only through this proxy.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Development server on port 3000 |
| `npm run build` / `npm start` | Production build and server |
| `npm run typecheck` | `next typegen` then `tsc --noEmit` |
| `npm run lint` | ESLint (Next.js core-web-vitals + TypeScript rules) |
| `npm test` | Vitest unit and component tests (`tests/`) |
| `npm run e2e` | Playwright end-to-end test (`e2e/`) against the real backend |

### End-to-end test

`e2e/production.spec.ts` drives a full production through the UI against the running backend in
mock mode: create a campaign, start production, approve the plan, approve compliance, and check
the job completes with a verified export and a stored delivery. Campaign names are unique per run.

```bash
npx playwright install chromium   # once
npm run e2e                       # starts `next dev` on port 3100 unless one is already there
E2E_BASE_URL=http://localhost:3000 npm run e2e   # or reuse a server you started
```

A mock production takes roughly 10 to 60 seconds.

## Structure

```
app/
  api/backend/[...path]/route.ts   server-side proxy (token, streaming, path guard)
  page.tsx, dashboard.tsx          production control: active jobs, approvals, system status
  campaigns/                       list, new (brief form), [id] (brief, versions, start production)
  jobs/[id]/                       layout = live job header, stage tally and sub-navigation
    page.tsx, monitor.tsx          16-stage rundown, controls, approvals, activity (SSE)
    plan/  storyboard/  scenes/  scenes/[key]/  qa/  delivery/
  browser/, browser/login/         Chrome/Vids session, modes, diagnostics, capability registry
  logs/                            audit trail and per-production AI requests
  settings/                        operator name, brand profile editor, effective configuration
components/
  ui/                              button, dialog (native <dialog>), fields, notice, section, status badge
  job/                             job context (SSE + revalidation), header, stage rundown
  shell/                           site header, system rail and simulation banner
  approval-card, artifact, brief-form, validation-list, job-row, system-status
lib/
  api.ts          fetch client, error normalisation, X-Actor
  proxy-path.ts   proxy path guard (pure, unit tested)
  job-events.ts   SSE reducer and hook with ?after= reconnects
  status.ts       every status vocabulary mapped to tone + label
  brief.ts        brief form state, client validation mirroring BriefInput
  operator.ts     operator name in localStorage
tests/            vitest
e2e/              playwright
```

## Honesty rules the UI follows

- A simulation banner is shown whenever the backend or the job reports `simulation: true`.
- Progress is `job.progress` from the server; there are no client-side timers or optimistic
  success states. Production actions update only after the server answers, and server error
  messages are shown as returned.
- There is no password field anywhere. Google sign-in happens by hand in the dedicated Chrome window.
