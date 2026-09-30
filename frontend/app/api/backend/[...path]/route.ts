// Server-side proxy to the Vids Production API.
//
// The browser never sees API_TOKEN: it calls /api/backend/<path> on this origin and this
// handler adds `Authorization: Bearer <API_TOKEN>` before forwarding to
// `${BACKEND_URL}/api/<path>`. Bodies are streamed in both directions, so multipart
// uploads and the Server-Sent Events stream (GET /api/jobs/{id}/events) pass through
// without buffering.

import type { NextRequest } from "next/server";
import {
  buildUpstreamUrl,
  FORWARDED_REQUEST_HEADERS,
  FORWARDED_RESPONSE_HEADERS,
} from "@/lib/proxy-path";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type Ctx = { params: Promise<{ path: string[] }> };

function errorResponse(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message, details: {} } }, { status });
}

async function forward(request: NextRequest, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params;
  const backend = process.env.BACKEND_URL || "http://localhost:8100";
  const target = buildUpstreamUrl(backend, path, request.nextUrl.search);
  if (!target) return errorResponse(400, "BAD_PATH", "that API path is not allowed");

  const token = process.env.API_TOKEN;
  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (token) headers.set("authorization", `Bearer ${token}`);
  const length = request.headers.get("content-length");
  if (length) headers.set("content-length", length);

  const hasBody = request.method !== "GET" && request.method !== "HEAD" && request.body !== null;
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      // Required by Node's fetch to stream a request body.
      ...(hasBody ? { duplex: "half" } : {}),
      cache: "no-store",
      redirect: "manual",
      signal: request.signal,
    } as RequestInit);
  } catch (err) {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    const reason = err instanceof Error ? err.message : "unknown error";
    return errorResponse(
      502,
      "BACKEND_UNREACHABLE",
      `The Vids Production API at ${new URL(backend).host} did not answer (${reason}). Check that the backend is running.`,
    );
  }

  const out = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  // Node's fetch decodes compressed bodies, so the upstream length no longer applies.
  if (upstream.headers.get("content-encoding")) out.delete("content-length");
  const isStream = (upstream.headers.get("content-type") || "").startsWith("text/event-stream");
  if (isStream) {
    out.set("cache-control", "no-cache, no-transform");
    out.set("x-accel-buffering", "no");
    out.set("connection", "keep-alive");
  }
  if (upstream.status === 401) {
    return errorResponse(
      502,
      "PROXY_UNAUTHORISED",
      "The backend rejected the dashboard's API token. Set API_TOKEN in frontend/.env.local to the backend's API_TOKEN.",
    );
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
