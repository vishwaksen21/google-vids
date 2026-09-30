// Live job updates over Server-Sent Events (GET /api/jobs/{id}/events via the proxy).
//
// The backend streams `audit` events (with ids) and `status` events. EventSource's own
// reconnect would replay from the start, so we reconnect ourselves with ?after=<last id>.

import { useEffect, useReducer, useRef } from "react";
import { apiUrl } from "./api";
import type { AuditEvent } from "./types";

export type Connection = "connecting" | "open" | "reconnecting" | "closed";

export interface StreamStatus {
  status: string;
  current_stage: string | null;
  control: string | null;
}

export interface EventsState {
  events: AuditEvent[]; // newest last, deduplicated by id
  lastId: number;
  status: StreamStatus | null;
  connection: Connection;
  retries: number;
  error: string | null;
}

export type EventsAction =
  | { type: "connecting" }
  | { type: "open" }
  | { type: "audit"; event: AuditEvent }
  | { type: "status"; status: StreamStatus }
  | { type: "error"; message: string }
  | { type: "closed" };

export const MAX_EVENTS = 300;

export const initialEventsState: EventsState = {
  events: [],
  lastId: 0,
  status: null,
  connection: "connecting",
  retries: 0,
  error: null,
};

export function eventsReducer(state: EventsState, action: EventsAction): EventsState {
  switch (action.type) {
    case "connecting":
      return { ...state, connection: state.retries > 0 ? "reconnecting" : "connecting" };
    case "open":
      return { ...state, connection: "open", retries: 0, error: null };
    case "audit": {
      const ev = action.event;
      if (ev.id <= state.lastId) return state; // replayed or duplicate
      const events = [...state.events, ev];
      if (events.length > MAX_EVENTS) events.splice(0, events.length - MAX_EVENTS);
      return { ...state, events, lastId: ev.id };
    }
    case "status":
      return { ...state, status: action.status };
    case "error":
      return { ...state, connection: "reconnecting", retries: state.retries + 1, error: action.message };
    case "closed":
      return { ...state, connection: "closed" };
    default:
      return state;
  }
}

/** Backoff for reconnects: 1s, 2s, 4s … capped at 15s. */
export function reconnectDelay(retries: number): number {
  return Math.min(15000, 1000 * 2 ** Math.max(0, retries - 1));
}

interface Options {
  /** Called (throttled by the caller) whenever anything arrives. */
  onEvent?: (kind: "audit" | "status") => void;
  /** Stop streaming (e.g. once the job is terminal). */
  enabled?: boolean;
}

export function useJobEvents(jobId: string, { onEvent, enabled = true }: Options = {}): EventsState {
  const [state, dispatch] = useReducer(eventsReducer, initialEventsState);
  const lastIdRef = useRef(0);
  const retriesRef = useRef(0);
  const onEventRef = useRef(onEvent);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    lastIdRef.current = state.lastId;
    retriesRef.current = state.retries;
  }, [state.lastId, state.retries]);

  useEffect(() => {
    if (!enabled || typeof EventSource === "undefined") {
      dispatch({ type: "closed" });
      return;
    }
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      dispatch({ type: "connecting" });
      const url = apiUrl(`jobs/${encodeURIComponent(jobId)}/events?after=${lastIdRef.current}`);
      source = new EventSource(url);
      source.onopen = () => dispatch({ type: "open" });
      source.addEventListener("audit", (e) => {
        try {
          const ev = JSON.parse((e as MessageEvent<string>).data) as AuditEvent;
          if (ev.id > lastIdRef.current) lastIdRef.current = ev.id;
          dispatch({ type: "audit", event: ev });
          onEventRef.current?.("audit");
        } catch {
          /* ignore malformed frame */
        }
      });
      source.addEventListener("status", (e) => {
        try {
          dispatch({ type: "status", status: JSON.parse((e as MessageEvent<string>).data) as StreamStatus });
          onEventRef.current?.("status");
        } catch {
          /* ignore malformed frame */
        }
      });
      source.onerror = () => {
        source?.close();
        source = null;
        if (stopped) return;
        dispatch({ type: "error", message: "Live updates were interrupted; reconnecting." });
        timer = setTimeout(connect, reconnectDelay(retriesRef.current + 1));
      };
    };

    connect();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      source?.close();
      dispatch({ type: "closed" });
    };
  }, [jobId, enabled]);

  return state;
}

/** Leading + trailing throttle: at most one call per `ms`, never drops the last one. */
export function throttle(fn: () => void, ms: number): (() => void) & { cancel: () => void } {
  let last = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const run = () => {
    last = Date.now();
    timer = null;
    fn();
  };
  const throttled = () => {
    const wait = ms - (Date.now() - last);
    if (wait <= 0) run();
    else if (!timer) timer = setTimeout(run, wait);
  };
  throttled.cancel = () => {
    if (timer) clearTimeout(timer);
    timer = null;
  };
  return throttled;
}
