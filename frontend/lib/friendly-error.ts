// Plain-language explanations for the errors a production can stop with. The raw code is
// still shown (small) for troubleshooting.

export interface ErrorLike {
  code?: string | null;
  message?: string | null;
}

const FRIENDLY: Record<string, string> = {
  LIVE_AUTOMATION_DISABLED:
    "Real generation is switched off, so the production stopped before making anything in Google Vids. Nothing was generated and no quota was used. To finish a practice run, use practice (mock) mode; to make real videos, turn real generation on.",
  SESSION_EXPIRED: "The Google sign-in has expired. Sign in again on the Google account page, then retry.",
  WRONG_ACCOUNT: "Chrome is signed in to a different Google account than the one allowed. Switch accounts on the Google account page, then retry.",
  QUOTA_EXHAUSTED: "This Google account has used up its Google Vids video generations. Use another account or wait for the limit to reset.",
  UNSAVED_CLIP:
    "A generated clip is open in the automation's Chrome window but could not be confirmed in the timeline. Insert it in that window; do not close Chrome.",
  UI_MISMATCH: "Google Vids did not look the way the automation expected, so it stopped instead of guessing. See Advanced → Logs for the screenshot.",
  CAPABILITY_UNAVAILABLE: "This step needs a Google Vids action that has not been verified yet, so it was not attempted.",
  GENERATION_REFUSED: "Google Vids refused to generate this clip. Change the scene's prompt and retry.",
  GENERATION_TIMEOUT: "Google Vids took too long to generate the clip. Retry the scene.",
};

/** The sentence to show a person, or the backend's own message when there is no better one. */
export function friendlyError(error: ErrorLike | null | undefined): string {
  if (!error) return "";
  return (error.code && FRIENDLY[error.code]) || error.message || "Something went wrong.";
}
