import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "@/components/ui/status-badge";
import { statusMeta } from "@/lib/status";

describe("statusMeta", () => {
  it("maps every stage status to a tone and a plain label", () => {
    expect(statusMeta("stage", "PASSED")).toEqual({ label: "Passed", tone: "done" });
    expect(statusMeta("stage", "PASSED_WITH_WARNINGS").tone).toBe("warn");
    expect(statusMeta("stage", "RUNNING").tone).toBe("live");
    expect(statusMeta("stage", "VALIDATING").tone).toBe("live");
    expect(statusMeta("stage", "RETRYING").tone).toBe("live");
    expect(statusMeta("stage", "WAITING_FOR_HUMAN")).toEqual({ label: "Waiting for a person", tone: "attention" });
    expect(statusMeta("stage", "FAILED").tone).toBe("fail");
    expect(statusMeta("stage", "PENDING").tone).toBe("idle");
    expect(statusMeta("stage", "SKIPPED").tone).toBe("muted");
    expect(statusMeta("stage", "CANCELLED").tone).toBe("muted");
  });

  it("maps scene, job and validation vocabularies", () => {
    expect(statusMeta("scene", "HUMAN_REVIEW").tone).toBe("attention");
    expect(statusMeta("scene", "ACCEPTED").tone).toBe("done");
    expect(statusMeta("scene", "REGENERATION_REQUIRED").label).toBe("Regeneration required");
    expect(statusMeta("job", "COMPLETED").tone).toBe("done");
    expect(statusMeta("job", "PAUSED").tone).toBe("attention");
    expect(statusMeta("outcome", "NOT_RUN").tone).toBe("muted");
    expect(statusMeta("severity", "BLOCKER").tone).toBe("fail");
    expect(statusMeta("capability", "REQUIRES_HUMAN").tone).toBe("attention");
  });

  it("never reports an unknown status as success", () => {
    const meta = statusMeta("stage", "SOMETHING_NEW");
    expect(meta.tone).toBe("idle");
    expect(meta.label).toBe("Something new");
    expect(statusMeta("job", null)).toEqual({ label: "Unknown", tone: "idle" });
  });
});

describe("StatusBadge", () => {
  it("shows the status as text, not only colour", () => {
    render(<StatusBadge kind="stage" value="WAITING_FOR_HUMAN" />);
    const badge = screen.getByText("Waiting for a person");
    expect(badge).toHaveAttribute("data-tone", "attention");
    expect(badge.querySelector("svg")).toHaveAttribute("aria-hidden");
  });

  it("accepts a label override", () => {
    render(<StatusBadge kind="stage" value="SKIPPED" label="Live automation disabled" />);
    expect(screen.getByText("Live automation disabled")).toHaveAttribute("data-status", "SKIPPED");
  });
});
