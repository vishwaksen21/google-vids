import { afterEach, describe, expect, it, vi } from "vitest";
import { apiRequest, OperatorRequiredError, parseError } from "@/lib/api";
import { setOperator } from "@/lib/operator";

describe("parseError", () => {
  it("reads domain errors", () => {
    const e = parseError(409, { error: { code: "CONFLICT", message: "this campaign already has an active production", details: {} } });
    expect(e.status).toBe(409);
    expect(e.code).toBe("CONFLICT");
    expect(e.message).toBe("this campaign already has an active production");
  });

  it("reads HTTPException details", () => {
    expect(parseError(400, { detail: "an X-Actor header naming the person acting is required" }).message).toMatch(/X-Actor/);
  });

  it("turns request validation errors into field errors", () => {
    const e = parseError(422, { detail: [{ loc: ["body", "brief", "raw_brief"], msg: "String should have at least 1 character" }] });
    expect(e.code).toBe("VALIDATION_ERROR");
    expect(e.fieldErrors).toEqual([{ path: "brief.raw_brief", message: "String should have at least 1 character" }]);
    expect(e.message).toContain("brief.raw_brief");
  });

  it("falls back for unknown bodies", () => {
    expect(parseError(502, "Bad gateway").message).toBe("Request failed with status 502");
  });
});

describe("apiRequest", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("blocks changes until an operator name is set", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(apiRequest("campaigns", { method: "POST", body: {} })).rejects.toBeInstanceOf(OperatorRequiredError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends X-Actor on changes, through the same-origin proxy", async () => {
    setOperator("Asha Rao");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "x" }), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    await apiRequest("campaigns", { method: "POST", body: { name: "n" } });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/backend/campaigns");
    expect(init.headers["x-actor"]).toBe("Asha Rao");
    expect(init.headers.authorization).toBeUndefined();
  });
});
