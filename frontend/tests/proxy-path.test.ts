import { describe, expect, it } from "vitest";
import { buildUpstreamUrl, isSafeSegment } from "@/lib/proxy-path";

const BASE = "http://localhost:8100";

describe("proxy path guard", () => {
  it("maps the catch-all path under /api/ and keeps the query string", () => {
    const url = buildUpstreamUrl(BASE, ["jobs", "01a0ed82-92ec-7571-a5ef-51aa34d6e4b7", "events"], "?after=42");
    expect(url?.toString()).toBe("http://localhost:8100/api/jobs/01a0ed82-92ec-7571-a5ef-51aa34d6e4b7/events?after=42");
  });

  it("tolerates a trailing slash on BACKEND_URL", () => {
    expect(buildUpstreamUrl(`${BASE}/`, ["system"], "")?.toString()).toBe("http://localhost:8100/api/system");
  });

  it.each([[[".."]], [["jobs", ".."]], [["..", "health"]], [["."]], [["a..b"]], [[""]], [["jobs/x"]], [["a\\b"]], [["%2e%2e"]], [["jobs", "x?y"]], [["http:", "", "evil.com"]]])(
    "rejects %j",
    (segments) => {
      expect(buildUpstreamUrl(BASE, segments as string[], "")).toBeNull();
    },
  );

  it("rejects an empty path", () => {
    expect(buildUpstreamUrl(BASE, [], "")).toBeNull();
    expect(buildUpstreamUrl(BASE, undefined, "")).toBeNull();
  });

  it("accepts ordinary identifiers", () => {
    for (const s of ["campaigns", "S01", "brand-profiles", "QA_REPORT", "login"]) expect(isSafeSegment(s)).toBe(true);
  });
});
