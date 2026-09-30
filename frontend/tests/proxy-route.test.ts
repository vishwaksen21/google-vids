// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET, POST } from "@/app/api/backend/[...path]/route";

const ctx = (path: string[]) => ({ params: Promise.resolve({ path }) });

describe("backend proxy route", () => {
  beforeEach(() => {
    process.env.API_TOKEN = "secret-token";
    process.env.BACKEND_URL = "http://backend.test:8100";
  });
  afterEach(() => vi.unstubAllGlobals());

  it("adds the bearer token on the server and forwards X-Actor and Idempotency-Key", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 201, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const req = new NextRequest("http://localhost:3000/api/backend/campaigns/c1/jobs?x=1", {
      method: "POST",
      headers: { "x-actor": "Asha", "idempotency-key": "k-1", "content-type": "application/json", cookie: "a=b", authorization: "Bearer from-browser" },
      body: "{}",
    });
    const res = await POST(req, ctx(["campaigns", "c1", "jobs"]));
    expect(res.status).toBe(201);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("http://backend.test:8100/api/campaigns/c1/jobs?x=1");
    const h = init.headers as Headers;
    expect(h.get("authorization")).toBe("Bearer secret-token");
    expect(h.get("x-actor")).toBe("Asha");
    expect(h.get("idempotency-key")).toBe("k-1");
    expect(h.get("cookie")).toBeNull();
  });

  it("refuses traversal without calling the backend", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const res = await GET(new NextRequest("http://localhost:3000/api/backend/x"), ctx(["..", "health"]));
    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("streams the upstream body (SSE) instead of buffering it", async () => {
    const stream = new ReadableStream({
      start(c) {
        c.enqueue(new TextEncoder().encode("event: status\ndata: {}\n\n"));
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(stream, { headers: { "content-type": "text/event-stream" } })));
    const res = await GET(new NextRequest("http://localhost:3000/api/backend/jobs/j/events"), ctx(["jobs", "j", "events"]));
    expect(res.headers.get("content-type")).toBe("text/event-stream");
    expect(res.headers.get("cache-control")).toContain("no-cache");
    const reader = res.body!.getReader();
    const first = await reader.read();
    expect(new TextDecoder().decode(first.value)).toContain("event: status");
    await reader.cancel();
  });

  it("reports an unreachable backend with a clear message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const res = await GET(new NextRequest("http://localhost:3000/api/backend/system"), ctx(["system"]));
    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.error.code).toBe("BACKEND_UNREACHABLE");
  });
});
