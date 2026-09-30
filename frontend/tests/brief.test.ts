import { describe, expect, it } from "vitest";
import { briefPayload, checkReferenceFile, emptyBriefForm, validateBriefForm } from "@/lib/brief";

function valid() {
  const f = emptyBriefForm();
  f.name = "Family floater launch";
  f.raw_brief = "A 30-second film introducing our family floater plan.";
  return f;
}

describe("validateBriefForm", () => {
  it("accepts a minimal brief", () => {
    expect(validateBriefForm(valid())).toEqual({});
  });

  it("requires a name and a brief", () => {
    const e = validateBriefForm(emptyBriefForm());
    expect(e.name).toBeDefined();
    expect(e["brief.raw_brief"]).toBeDefined();
  });

  it("skips the name when editing an existing campaign", () => {
    const f = valid();
    f.name = "";
    expect(validateBriefForm(f, { requireName: false })).toEqual({});
  });

  it("enforces the schema's length limits", () => {
    const f = valid();
    f.raw_brief = "x".repeat(4001);
    f.cta = "y".repeat(301);
    const e = validateBriefForm(f);
    expect(e["brief.raw_brief"]).toMatch(/4000/);
    expect(e["brief.cta"]).toMatch(/300/);
  });

  it("checks duration bounds and their order", () => {
    const f = valid();
    f.min_duration = "40";
    f.max_duration = "20";
    expect(validateBriefForm(f)["brief.output.max_duration_seconds"]).toMatch(/at least the minimum/);
    f.max_duration = "601";
    expect(validateBriefForm(f)["brief.output.max_duration_seconds"]).toMatch(/between 1 and 600/);
    f.min_duration = "abc";
    expect(validateBriefForm(f)["brief.output.min_duration_seconds"]).toBeDefined();
  });

  it("requires text on every claim and disclosure", () => {
    const f = valid();
    f.claims = [{ text: " ", fact_id: "PRU-HEALTH-FF-001" }];
    f.disclosures = [{ text: "", on_screen: true, spoken: false }];
    const e = validateBriefForm(f);
    expect(e["brief.required_claims.0.text"]).toBeDefined();
    expect(e["brief.disclosures.0.text"]).toBeDefined();
  });
});

describe("briefPayload", () => {
  it("trims text, nulls empty optional fields and converts durations", () => {
    const f = valid();
    f.objective = "  Awareness ";
    f.min_duration = "20";
    f.claims = [{ text: "One policy covers every member", fact_id: "" }];
    const p = briefPayload(f);
    expect(p.objective).toBe("Awareness");
    expect(p.audience).toBeNull();
    expect(p.output.min_duration_seconds).toBe(20);
    expect(p.output.max_duration_seconds).toBeNull();
    expect(p.required_claims[0].fact_id).toBeNull();
    expect(p.output.aspect_ratio).toBe("16:9");
  });
});

describe("checkReferenceFile", () => {
  it("accepts PNG and JPEG up to 10 MB, at most 7", () => {
    expect(checkReferenceFile({ type: "image/png", size: 1000 }, 0)).toBeNull();
    expect(checkReferenceFile({ type: "image/gif", size: 1000 }, 0)).toMatch(/PNG or JPEG/);
    expect(checkReferenceFile({ type: "image/jpeg", size: 10 * 1024 * 1024 + 1 }, 0)).toMatch(/10 MB/);
    expect(checkReferenceFile({ type: "image/jpeg", size: 1 }, 7)).toMatch(/at most 7/);
  });
});
