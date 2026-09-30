// Path guard for the server-side proxy. Pure so it can be unit tested.
//
// The browser calls /api/backend/<path>; the proxy forwards to `${BACKEND_URL}/api/<path>`.
// Only plain path segments are allowed: no traversal, no empty segments, no encoded
// separators, nothing that could make the upstream URL leave /api/.

const SEGMENT = /^[A-Za-z0-9_\-.:~]+$/;

export function isSafeSegment(segment: string): boolean {
  if (segment.length === 0 || segment.length > 200) return false;
  if (segment === "." || segment === "..") return false;
  if (segment.includes("..")) return false;
  return SEGMENT.test(segment);
}

/**
 * Builds the upstream URL for a proxied request, or returns null when the path is not
 * allowed. `segments` are the decoded catch-all params from the route.
 */
export function buildUpstreamUrl(
  backendUrl: string,
  segments: readonly string[] | undefined,
  search: string,
): URL | null {
  if (!segments || segments.length === 0 || segments.length > 12) return null;
  if (!segments.every(isSafeSegment)) return null;
  const base = backendUrl.replace(/\/+$/, "");
  let url: URL;
  try {
    url = new URL(`${base}/api/${segments.join("/")}`);
  } catch {
    return null;
  }
  const origin = new URL(base).origin;
  if (url.origin !== origin || !url.pathname.startsWith("/api/")) return null;
  if (search) url.search = search.startsWith("?") ? search : `?${search}`;
  return url;
}

/** Request headers the browser may pass through to the backend. */
export const FORWARDED_REQUEST_HEADERS = [
  "accept",
  "content-type",
  "x-actor",
  "idempotency-key",
  "range",
  "if-none-match",
  "if-modified-since",
] as const;

/** Response headers passed back to the browser. */
export const FORWARDED_RESPONSE_HEADERS = [
  "content-type",
  "content-length",
  "content-range",
  "accept-ranges",
  "content-disposition",
  "cache-control",
  "etag",
  "last-modified",
  "x-content-type-options",
] as const;
