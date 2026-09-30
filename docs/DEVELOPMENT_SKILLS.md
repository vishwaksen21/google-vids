# Development Skills

Skills that Claude Code loads while building this project. Installed on 2026-09-29 with the skills CLI (`npx skills add <owner/repo> --skill <name> --agent claude-code -y`). Every source below came from `npx skills find` results; none were guessed.

Project-level installs live in `vids/.claude/skills/` and are pinned by hash in `vids/skills-lock.json`. Restore them with `npx skills experimental_install`.

## Installed for this project

| Skill | Source | Purpose here | Phases |
|---|---|---|---|
| `playwright-cli` | `microsoft/playwright-cli` | Interactive exploration of the real Google Vids UI: snapshots with element refs, recording actions as Playwright code, saving storage state. The main tool for Phase 4 reconnaissance. | 4, 5, 6, 7 |
| `agent-browser` | `vercel-labs/agent-browser` | Second option for accessibility-tree exploration of the Vids editor when Playwright snapshots are noisy. Used for exploration only, not in the runtime adapter. | 4 |
| `webapp-testing` | `anthropics/skills` | Python Playwright recon-then-act pattern, plus `with_server.py` to run backend and frontend together for dashboard E2E tests. | 8, 9 |
| `frontend-design` | `anthropics/skills` | Design-plan-then-critique process for the dashboard. It defers to our brief, and `../DESIGN.md` (Prudential brand system) is that brief. | 1, 8 |
| `vercel-react-best-practices` | `vercel-labs/agent-skills` | Next.js App Router and React performance rules: server components, data fetching, bundle size. | 1, 8 |
| `web-design-guidelines` | `vercel-labs/agent-skills` | UI review pass against the Web Interface Guidelines. | 8 |
| `accessibility` | `addyosmani/web-quality-skills` | WCAG 2.2 audit for the dashboard controls (pause, approve, reject) and status displays. | 8, 9 |
| `tailwind-design-system` | `wshobson/agents` | Tailwind v4 design tokens. Used to encode the Prudential red and gray scales as tokens. | 1, 8 |
| `fastapi` | `fastapi/fastapi` (official) | FastAPI conventions: `Annotated` dependencies, return-type response models, and SSE for live production progress. | 1, 3, 8 |
| `python-error-handling` | `wshobson/agents` | Exception hierarchy and partial-failure handling for the workflow engine and browser adapter. | 1, 3, 4 |
| `api-and-interface-design` | `addyosmani/agent-skills` | Stable REST contract between the dashboard and backend, and the port interface for the Vids adapter. | 1, 3 |
| `gemini-api-dev` | `google-gemini/gemini-skills` | Current Gemini models and SDK (`google-genai`, Interactions API, structured output, multimodal video inspection). Its rule to fetch the live docs before writing code applies. | 2, 5, 7 |
| `langgraph-fundamentals` | `langchain-ai/langchain-skills` | Reference only. Kept in case the Phase 0 decision goes to LangGraph. The proposed design uses an explicit Postgres-backed state machine instead (see `ARCHITECTURE.md`). | 3 (conditional) |
| `langgraph-human-in-the-loop` | `langchain-ai/langchain-skills` | Reference for interrupt and approval patterns; the same ideas apply to our own approval gates. | 3 (conditional) |
| `tdd` | `mattpocock/skills` | Red-green-refactor for the workflow engine, validation gates, and planners. | 1–9 |
| `supabase-postgres-best-practices` | `supabase/agent-skills` | General Postgres guidance (indexes, constraints, locking). It states it applies to Postgres anywhere; we do not use Supabase. | 1, 3 |
| `improve-codebase-architecture` | `mattpocock/skills` | End-of-phase architecture review. | after each phase |

## Already available at user level (not reinstalled)

These were already in `~/.claude/skills/`, so project copies would have been duplicates.

| Skill | Purpose here | Phases |
|---|---|---|
| `playwright-best-practices` (`currents-dev`) | Locator strategy, auto-waiting, flake avoidance, auth state reuse. Governs the runtime Vids adapter. | 4–9 |
| `fastapi-templates` | Project layout and dependency-injection scaffolding | 1 |
| `postgresql-table-design` | Schema design: types, constraints, indexes | 1, 3 |
| `pytest-coverage` | Coverage gaps in backend tests | all |
| `security-and-hardening` (`addyosmani`) | OWASP, input handling, secrets, dependency audit | 1, 4, 7, 9 |
| `clean-architecture` | Ports and adapters boundary between domain, workflow, and the Vids adapter | 0, 1, 4 |
| `computer-vision-opencv` | Frame sampling and blank or black-frame checks in scene and export QA | 5, 7 |

Built-in Claude Code skills that cover the other categories: `code-review`, `security-review`, `simplify`.

Skills inherited from the parent `media-generation/.claude/skills/` (`ffmpeg`, `storyboard-creation`, `video-prompting-guide`, `domain-modeling`, `temporal-developer`) are visible when Claude Code runs from the parent directory. `ffmpeg` (ffprobe) is used for export verification in Phase 7, and `video-prompting-guide` and `storyboard-creation` inform Phase 2.

## Security review of installed skills

- The installer runs third-party risk scans (Gen, Socket, Snyk) and shows them per skill on its skills.sh page, e.g. https://skills.sh/microsoft/playwright-cli/playwright-cli. `playwright-cli` scored Gen: Safe, Socket: 1 alert, Snyk: Med Risk. The Snyk rating reflects that a browser-automation tool is powerful by nature; no malicious pattern was found.
- Only `webapp-testing` ships executable files (`scripts/with_server.py` and three examples, from Anthropic's repo). No skill contains `curl | sh`, `eval(`, `rm -rf`, or base64-decoded payloads (checked with grep).
- Skills run with full agent permissions. Review the diff whenever `npx skills update` changes a pinned hash.

## Unavailable or deliberately not installed

| Wanted | Result |
|---|---|
| Alembic / database migrations | Only low-adoption skills exist (under 2K installs, unknown maintainers). Not installed. Use Alembic's own docs plus `postgresql-table-design`. |
| SQLAlchemy 2.x async | No well-maintained skill found. Use the official docs. |
| Secret management | No general-purpose skill; the results were vendor-specific (Convex, Go). `security-and-hardening` covers the basics. |
| Framer Motion | Only low-adoption skills. Motion is minimal under `DESIGN.md` (150–600 ms, purposeful), so no skill is needed. |
| `browser-use/browser-use` | Available but not installed. It is an LLM-driven browsing agent; our adapter must be deterministic and verifiable, so free-form agent browsing would work against the validation model. |
| `anti-detect-browser` and similar | Rejected. Evading detection conflicts with Google's policies and with this project's security requirements. |
| `mattpocock/skills@code-review`, `getsentry/skills@security-review` | Skipped because the built-in `code-review` and `security-review` skills already cover them. |

## Remaining prerequisites

- **Python 3.12+**: the system Python is 3.9.6. `uv` is installed and will provide 3.12.
- **Playwright browsers**: `uv run playwright install chromium`, plus a dedicated Chrome profile for Vids (Phase 4).
- **PostgreSQL server**: `postgresql@16` is installed via Homebrew but not running (`brew services start postgresql@16`), and there is no `vids` database yet.
- **Google Chrome**: installed, so the dedicated automation profile can use the real Chrome channel.
- **Redis**: not installed. It is not required for the proposed design (see `ARCHITECTURE.md`).
- **Docker**: not installed. The prompt does not require it.
- **pnpm**: not installed. The frontend will use npm unless you prefer pnpm.
- **Gemini API key**: needed from Phase 2.
- **Google Workspace account with Vids and Omni access**: needed from Phase 4, along with written approval to automate it (see the Phase 0 risks).

## Applied during implementation (2026-09-29)

| Skill | Where it shaped the code |
|---|---|
| `fastapi` | `Annotated` dependencies (`api/deps.py`), return-type response models, `fastapi.sse.EventSourceResponse` for live job events. SQLModel was not used: the engine needs `FOR UPDATE SKIP LOCKED`, partial unique indexes and triggers |
| `supabase-postgres-best-practices` | text + CHECK instead of enum types, `timestamptz`, indexed foreign keys, time-ordered UUIDv7 keys, `SKIP LOCKED` claims, short transactions, statement and idle-in-transaction timeouts |
| `gemini-api-dev` | the direct provider follows the Interactions API docs fetched on 2026-09-29 (`input` parts, `response_format` with a JSON schema, `store=false`); model ids are configuration |
| `playwright-best-practices`, `playwright-cli` | locator priority role → label → placeholder → test id → CSS, never coordinates; web-first waits; persistent-context auth; fixture-based adapter tests |
| `python-error-handling` | the error hierarchy in `errors.py` encodes the recovery policy |
| `security-and-hardening` | see SECURITY.md |
| `tdd`, `pytest-coverage` | workflow behaviour written as tests first where the rule was clear (gates, policy, planner) |
| `frontend-design`, `vercel-react-best-practices`, `tailwind-design-system`, `accessibility`, `web-design-guidelines` | handed to the dashboard build together with DESIGN.md |
