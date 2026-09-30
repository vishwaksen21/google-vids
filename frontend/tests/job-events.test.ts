import { describe, expect, it } from "vitest";
import { eventsReducer, initialEventsState, MAX_EVENTS, reconnectDelay, type EventsState } from "@/lib/job-events";
import type { AuditEvent } from "@/lib/types";

const ev = (id: number, type = "stage.started"): AuditEvent => ({
  id,
  job_id: "j",
  campaign_id: "c",
  actor: "system",
  type,
  message: `event ${id}`,
  data: {},
  created_at: "2026-09-29T14:12:32Z",
});

describe("eventsReducer", () => {
  it("appends audit events in order and tracks the last id", () => {
    let s: EventsState = initialEventsState;
    s = eventsReducer(s, { type: "audit", event: ev(3) });
    s = eventsReducer(s, { type: "audit", event: ev(4) });
    expect(s.events.map((e) => e.id)).toEqual([3, 4]);
    expect(s.lastId).toBe(4);
  });

  it("drops replayed or duplicate events after a reconnect", () => {
    let s: EventsState = initialEventsState;
    for (const id of [1, 2, 3]) s = eventsReducer(s, { type: "audit", event: ev(id) });
    const before = s;
    s = eventsReducer(s, { type: "audit", event: ev(2) });
    s = eventsReducer(s, { type: "audit", event: ev(3) });
    expect(s).toBe(before);
  });

  it("caps the buffer at MAX_EVENTS, keeping the newest", () => {
    let s: EventsState = initialEventsState;
    for (let id = 1; id <= MAX_EVENTS + 25; id++) s = eventsReducer(s, { type: "audit", event: ev(id) });
    expect(s.events).toHaveLength(MAX_EVENTS);
    expect(s.events[0].id).toBe(26);
    expect(s.lastId).toBe(MAX_EVENTS + 25);
  });

  it("records status events and connection state", () => {
    let s = eventsReducer(initialEventsState, { type: "open" });
    expect(s.connection).toBe("open");
    s = eventsReducer(s, { type: "status", status: { status: "WAITING_FOR_HUMAN", current_stage: "PLAN_APPROVAL", control: null } });
    expect(s.status?.current_stage).toBe("PLAN_APPROVAL");
    s = eventsReducer(s, { type: "error", message: "interrupted" });
    expect(s.connection).toBe("reconnecting");
    expect(s.retries).toBe(1);
    s = eventsReducer(s, { type: "connecting" });
    expect(s.connection).toBe("reconnecting");
    s = eventsReducer(s, { type: "open" });
    expect(s.retries).toBe(0);
    expect(s.error).toBeNull();
  });

  it("backs off between reconnects, capped at 15 seconds", () => {
    expect(reconnectDelay(1)).toBe(1000);
    expect(reconnectDelay(2)).toBe(2000);
    expect(reconnectDelay(3)).toBe(4000);
    expect(reconnectDelay(10)).toBe(15000);
  });
});
