# Security

## What this system never does

- It **never asks for, types, stores or logs a Google password**. A person signs in by hand in
  the dedicated Chrome window (`vidsprod browser login`, or **Open sign-in window** on the
  dashboard's Login page). There is no password setting and no password field anywhere.
- It **never reads cookies or tokens**. The Google session lives only inside the Chrome profile
  directory (`var/browser-profile`, created with owner-only `0700` permissions and corrected if
  loosened). The application reads page content, never browser storage.
- It **never evades or bypasses Google's controls**. There are no stealth plugins, no
  fingerprint spoofing and no CAPTCHA or verification bypass. If Google shows a verification
  or "unusual traffic" page, the session state becomes `CHALLENGE` and the job waits for a
  person. Actions are paced at a human interval (`BROWSER_MIN_ACTION_INTERVAL_MS`), one at a
  time per browser session.
- It **never lets a model drive the browser**. Browser actions are a fixed, allowlisted set of
  recipes over catalogued locators, each followed by verification. LLM output never becomes
  code, selectors or clicks.

## Authorisation boundaries (defaults are safe)

| Boundary | Default | Effect |
|---|---|---|
| `GOOGLE_VIDS_MODE` | `mock` | No browser at all. `browser_test` launches Chrome read-only. `live` is required for any action that creates, submits, edits or exports |
| `GOOGLE_VIDS_LIVE_AUTOMATION_ENABLED` | `false` | Even in `live` mode, every mutating adapter call is refused until this is `true`. Enable it only after the account owner confirms that automating the account is permitted (Google's terms and, for Workspace accounts, the admin's policy) |
| `GOOGLE_VIDS_ALLOWED_ACCOUNT` | unset | Required for `browser_test`/`live`. The session must be signed in as exactly this account, or the job waits (`WRONG_ACCOUNT`) |
| `GOOGLE_VIDS_PROJECT_PREFIX` | `[vidsprod]` | The agent creates projects with this prefix and refuses to open or edit any Vid without it, so it cannot touch someone's existing work |
| Capability registry | all live capabilities `UNVERIFIED` | An operation runs only when recon has verified it with evidence against the current UI fingerprint. A UI change demotes capabilities automatically |
| `GEMINI_DIRECT_ENABLED` | `false` | Campaign content goes only to the company gateway until direct Gemini data handling is approved; direct calls use `store=false` |
| `AI_PROVIDER=fake`, `GOOGLE_VIDS_MODE=mock` | allowed in development | Refused at startup when `APP_ENV=production` |

## API

- Every `/api` route requires `Authorization: Bearer $API_TOKEN` (compared in constant time).
  The dashboard calls the backend through a server-side proxy, so the token never reaches the
  browser. `API_TOKEN` is mandatory in production.
- Every change requires an `X-Actor` header naming the person acting. It is recorded on every
  audit event and approval. **Limitation:** there is no identity provider yet, so the actor is
  declared, not authenticated. Put the dashboard behind company SSO before multi-user use.
- Request bodies are limited (`MAX_REQUEST_BYTES`; reference uploads 10 MB). Uploads must be
  PNG/JPEG and are checked by content signature, not just the declared type.
- Artifacts are served by id only. Stored paths are resolved strictly inside the artifact root
  (no traversal), with `X-Content-Type-Options: nosniff` and a safe content type.
- Security headers: `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `nosniff`. CORS is
  limited to `CORS_ORIGINS`.
- Errors return a code and a message. Tracebacks are kept in the stage record for engineers
  and are never returned by the API.

## Data

- **Least privilege database role:** `vidsprod` is not a superuser and cannot create roles or
  databases. Statement and idle-in-transaction timeouts are set per connection.
- **Integrity held by the database:** triggers make accepted (locked) takes immutable and the
  audit trail append-only. Partial unique indexes allow one active production per campaign,
  one running browser job per Google account, one pending intent per target and one pending
  approval per subject.
- **Logs:** JSON lines. Keys that look sensitive (`password`, `token`, `api_key`,
  `authorization`, `cookie`, `session`, …) and values that look like bearer tokens, API-key
  query parameters, cookie headers or database passwords are redacted. URLs are logged without
  query strings. The Google account is shown masked (`vi***@example.com`).
- **AI requests** are tracked by a hash of their content, not the content itself.
- **Secrets** live in `backend/.env` (mode `0600`, gitignored) or the deployment's secret store.
  `.env.example` documents every setting without values.

## Compliance for insurance content

- Product claims must cite an **approved fact** (`fact_id`) from the brand profile. An uncited
  or unknown claim blocks intake. Figures that do not appear in the cited fact block the script.
- Forbidden language (guarantees, superlatives, fear selling) is checked in the brief, concept
  and script.
- Standard disclosures are added deterministically for regulated brands and must appear on
  screen verbatim for at least their reading time (checked on the export).
- **Plan approval** and **compliance approval** are required gates. An approval records who
  decided, why, and the hashes of what was approved; if the approved content changes, the
  approval no longer applies.
- Simulated output (mock adapter) is marked everywhere and can never be submitted.

## Reporting

Security issues: contact the platform owner. Do not open public issues with session material,
tokens or screenshots of signed-in pages.
