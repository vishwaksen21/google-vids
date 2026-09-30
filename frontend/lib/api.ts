// Browser-side API client. Every request goes to the same-origin proxy
// (/api/backend/...), which adds the bearer token on the server.

import { getOperator, isValidOperator } from "./operator";

export const API_BASE = "/api/backend";

export interface FieldError {
  path: string;
  message: string;
}

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  fieldErrors: FieldError[];

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}, fieldErrors: FieldError[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.fieldErrors = fieldErrors;
  }
}

/** Raised before a mutating request when no operator name is set. */
export class OperatorRequiredError extends ApiError {
  constructor() {
    super(0, "OPERATOR_REQUIRED", "Enter your name before making changes. Every change is recorded against a person.");
    this.name = "OperatorRequiredError";
  }
}

type PydanticIssue = { loc?: (string | number)[]; msg?: string };

/**
 * Normalises the backend's three error shapes:
 *   {error: {code, message, details}}   domain errors
 *   {detail: "message"}                 HTTPException (401/400)
 *   {detail: [{loc, msg}]}              request validation (422)
 */
export function parseError(status: number, body: unknown): ApiError {
  if (body && typeof body === "object") {
    const obj = body as Record<string, unknown>;
    const err = obj.error;
    if (err && typeof err === "object") {
      const e = err as Record<string, unknown>;
      return new ApiError(
        status,
        typeof e.code === "string" ? e.code : `HTTP_${status}`,
        typeof e.message === "string" ? e.message : `Request failed (${status})`,
        (e.details as Record<string, unknown>) || {},
      );
    }
    const detail = obj.detail;
    if (typeof detail === "string") return new ApiError(status, `HTTP_${status}`, detail);
    if (Array.isArray(detail)) {
      const fieldErrors: FieldError[] = (detail as PydanticIssue[]).map((d) => ({
        path: (d.loc || []).filter((p) => p !== "body").join("."),
        message: d.msg || "invalid value",
      }));
      const summary = fieldErrors
        .slice(0, 3)
        .map((f) => (f.path ? `${f.path}: ${f.message}` : f.message))
        .join("; ");
      return new ApiError(status, "VALIDATION_ERROR", summary || "The request was not valid", {}, fieldErrors);
    }
  }
  return new ApiError(status, `HTTP_${status}`, `Request failed with status ${status}`);
}

export function apiUrl(path: string): string {
  return `${API_BASE}/${path.replace(/^\/+/, "")}`;
}

export function artifactContentUrl(id: string): string {
  return apiUrl(`artifacts/${encodeURIComponent(id)}/content`);
}

async function readBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH";
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

export async function apiRequest<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method || "GET";
  const headers: Record<string, string> = { accept: "application/json", ...(opts.headers || {}) };
  let body: BodyInit | undefined;
  if (method !== "GET") {
    const actor = getOperator();
    if (!isValidOperator(actor)) throw new OperatorRequiredError();
    headers["x-actor"] = actor.trim();
    if (opts.body instanceof FormData) {
      body = opts.body;
    } else if (opts.body !== undefined) {
      headers["content-type"] = "application/json";
      body = JSON.stringify(opts.body);
    }
  }
  let res: Response;
  try {
    res = await fetch(apiUrl(path), { method, headers, body, signal: opts.signal, cache: "no-store" });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "NETWORK", "The dashboard server could not be reached. Check your connection.");
  }
  const data = await readBody(res);
  if (!res.ok) throw parseError(res.status, data);
  return data as T;
}

/** SWR fetcher: the key is the API path (without /api/). */
export const fetcher = <T,>(path: string): Promise<T> => apiRequest<T>(path);

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Something went wrong";
}
