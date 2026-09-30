import { describe, expect, it } from "vitest";
import { friendlyError } from "@/lib/friendly-error";

describe("friendlyError", () => {
  it("explains a production stopped by browser-test mode in plain words", () => {
    const text = friendlyError({ code: "LIVE_AUTOMATION_DISABLED", message: "BROWSER_TEST mode: the session is ready, but production actions are disabled." });
    expect(text).toMatch(/Real generation is switched off/);
    expect(text).toMatch(/no quota was used/);
    expect(text).not.toMatch(/BROWSER_TEST/);
  });

  it("falls back to the backend's message for codes it does not know", () => {
    expect(friendlyError({ code: "SOMETHING_NEW", message: "a new failure" })).toBe("a new failure");
    expect(friendlyError(null)).toBe("");
  });
});
