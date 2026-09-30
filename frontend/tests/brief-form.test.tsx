import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SWRConfig } from "swr";
import { describe, expect, it, vi } from "vitest";
import { BriefForm } from "@/components/brief-form";
import { emptyBriefForm } from "@/lib/brief";

const profiles = [
  {
    id: "p1",
    name: "Prudential",
    approved_facts: [{ fact_id: "PRU-HEALTH-FF-001", text: "One policy covers every member of the family named on it.", source: "catalogue" }],
    standard_disclosures: ["Insurance is the subject matter of solicitation."],
  },
];

function renderForm(onSubmit = vi.fn().mockResolvedValue({})) {
  render(
    <SWRConfig value={{ provider: () => new Map(), fetcher: async () => profiles, dedupingInterval: 0 }}>
      <BriefForm initial={emptyBriefForm()} withName submitLabel="Create campaign" onSubmit={onSubmit} />
    </SWRConfig>,
  );
  return onSubmit;
}

describe("BriefForm", () => {
  it("shows field errors and does not submit an empty brief", async () => {
    const onSubmit = renderForm();
    await userEvent.click(screen.getByRole("button", { name: "Create campaign" }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText(/Check 2 fields/)).toBeInTheDocument();
    expect(screen.getByLabelText("Campaign name")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Brief")).toHaveAttribute("aria-invalid", "true");
  });

  it("fills a claim from the chosen approved fact and submits the payload", async () => {
    const onSubmit = renderForm();
    await userEvent.type(screen.getByLabelText("Campaign name"), "Family floater launch");
    await userEvent.type(screen.getByLabelText("Brief"), "A calm 30-second film for young parents.");
    await userEvent.click(screen.getByRole("button", { name: "More options" }));
    await userEvent.click(screen.getByRole("button", { name: "Add a claim" }));
    await screen.findByRole("option", { name: /PRU-HEALTH-FF-001/ });
    await userEvent.selectOptions(screen.getByLabelText("Approved fact"), "PRU-HEALTH-FF-001");
    expect(screen.getByLabelText("Claim 1")).toHaveValue("One policy covers every member of the family named on it.");
    await userEvent.click(screen.getByRole("button", { name: "Create campaign" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const [name, brief] = onSubmit.mock.calls[0];
    expect(name).toBe("Family floater launch");
    expect(brief.required_claims).toEqual([{ text: "One policy covers every member of the family named on it.", fact_id: "PRU-HEALTH-FF-001" }]);
    expect(brief.output.captions_required).toBe(true);
  });

  it("keeps the optional sections folded away until asked for", async () => {
    renderForm();
    const toggle = screen.getByRole("button", { name: "More options" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: "Add a claim" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Campaign name")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Landscape (16:9)" })).toBeChecked();
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "Add a claim" })).toBeInTheDocument();
  });

  it("opens the optional sections when one of their fields has a problem", async () => {
    renderForm();
    await userEvent.type(screen.getByLabelText("Campaign name"), "Family floater launch");
    await userEvent.type(screen.getByLabelText("Brief"), "A calm 30-second film for young parents.");
    await userEvent.click(screen.getByRole("button", { name: "More options" }));
    await userEvent.type(screen.getByLabelText(/Minimum duration/), "40");
    await userEvent.type(screen.getByLabelText(/Maximum duration/), "20");
    await userEvent.click(screen.getByRole("button", { name: "More options" }));  // fold it away again
    await userEvent.click(screen.getByRole("button", { name: "Create campaign" }));
    expect(screen.getByRole("button", { name: "More options" })).toHaveAttribute("aria-expanded", "true");
  });

  it("sends written scenes word for word and switches off what Vids cannot add yet", async () => {
    const onSubmit = renderForm();
    await userEvent.type(screen.getByLabelText("Campaign name"), "Written scenes");
    await userEvent.type(screen.getByLabelText("Brief"), "Two scenes for Prudential Health India.");
    await userEvent.click(screen.getByRole("button", { name: "Add a scene" }));
    await userEvent.type(screen.getByLabelText("Scene 1: what Google Vids should show"), "A calm sunlit family kitchen, slow push in.");
    await userEvent.type(screen.getByLabelText(/Scene 1: words on screen/), "Health insurance is about more than medical bills.");
    await userEvent.selectOptions(screen.getByLabelText("Scene 1: length"), "10");
    expect(screen.getByText(/Captions, voiceover and the end card are switched off/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create campaign" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const [, brief] = onSubmit.mock.calls[0];
    expect(brief.scenes).toEqual([
      { prompt: "A calm sunlit family kitchen, slow push in.", on_screen_text: "Health insurance is about more than medical bills.", duration_seconds: 10, photo_asset_id: null },
    ]);
    expect(brief.output).toMatchObject({ captions_required: false, voiceover_required: false, end_card_required: false });
  });

  it("does not submit a scene without a description", async () => {
    const onSubmit = renderForm();
    await userEvent.type(screen.getByLabelText("Campaign name"), "Written scenes");
    await userEvent.type(screen.getByLabelText("Brief"), "One scene.");
    await userEvent.click(screen.getByRole("button", { name: "Add a scene" }));
    await userEvent.click(screen.getByRole("button", { name: "Create campaign" }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Scene 1: what Google Vids should show")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: "More options" })).toHaveAttribute("aria-expanded", "false");
  });
});
