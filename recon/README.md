# Recon: verifying Google Vids capabilities

Nothing the agent does in the live Vids editor is allowed until it has been **observed** here.
This directory holds the two files that make an operation executable:

- `locators.json`: the locator catalogue (keys → role/label/placeholder/test-id/CSS locators),
  the landmark keys used for the UI fingerprint, and the fingerprint they produced.
- `capabilities.json`: capability entries marked `VERIFIED`, each with `verified_on`,
  `ui_fingerprint` and `evidence` (paths to the screenshots and traces of the passing run).
  An entry claiming `VERIFIED` without all three is ignored.

Neither file exists yet, so every live capability is `UNVERIFIED` and live production refuses
to run. That is the intended state until recon is done.

## Procedure

1. **Sign in** (a person, by hand):
   ```bash
   cd backend
   GOOGLE_VIDS_MODE=browser_test uv run vidsprod browser login
   ```
   A Chrome window opens on Google Vids. Sign in as `GOOGLE_VIDS_ALLOWED_ACCOUNT`. The command
   reports `SIGNED_IN` when it sees the account. The password is never seen by this tool.
2. **Discover** (read-only) on each screen you need, which you navigate to by hand in that window:
   ```bash
   GOOGLE_VIDS_MODE=browser_test uv run vidsprod browser diagnose --label home
   ```
   This writes a screenshot, the accessibility snapshot and a candidate-locator report to
   `recon/<date>/`. Candidates are role + accessible name pairs; `usable: false` means the name
   is ambiguous on that page.
3. **Map** keys to locators. `uv run vidsprod recon template` prints every key the recipes need,
   grouped by capability. Copy it to `recon/locators.json` and fill in the entries from the
   diagnostics, preferring `role`, then `label`/`placeholder`, then `testid`, and `css` only
   when nothing else is stable. Never coordinates. Choose `fingerprint_landmarks`: stable
   top-level controls whose presence defines "the editor as verified".
4. **Verify** each capability with a live run in `live` mode with authorisation enabled, on a
   scratch Vid whose title starts with `[vidsprod]`. Record the run's screenshots and the
   driver trace, and only then add the `VERIFIED` entry to `capabilities.json` with the evidence
   paths and fingerprint. Generation spends quota: verify `clip.generate` once, not repeatedly.
5. **Re-verify** whenever the session check reports `UI_CHANGED`. The fingerprint no longer
   matches and dependent capabilities have been demoted automatically.

## Open questions recon must answer

These are listed in `GOOGLE_VIDS_AUTOMATION.md` §3. Examples: the prompt box's character limit,
whether duration is set in whole seconds, whether Recreate/Extend/edits cost a generation,
how a generated clip can be identified and captured for inspection, and how the timeline
exposes scene order and durations for read-back.
