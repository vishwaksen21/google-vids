import { expect, test, type Page } from "@playwright/test";

// Full production against the real backend in mock mode: create a campaign through the
// UI, start production, approve the plan, approve compliance, and see it delivered.

const OPERATOR = "E2E Operator";

async function decide(page: Page, kind: string, button: RegExp) {
  const card = page.locator(`article[data-approval-kind="${kind}"]`).first();
  await card.getByLabel("Reason for your decision").fill(`Approved by the end-to-end test (${kind})`);
  await card.getByRole("button", { name: button }).click();
  await expect(card).toBeHidden({ timeout: 30_000 });
}

/** Mock runs normally resolve scenes on their own; accept any scene sent for review. */
async function acceptSceneReviews(page: Page, jobUrl: string) {
  const review = page.locator('article[data-approval-kind="SCENE_REVIEW"]').first();
  if (!(await review.isVisible())) return false;
  await review.getByRole("link", { name: /Review scene/ }).click();
  await page.getByRole("radio", { name: /^Accept/ }).check();
  await page.getByLabel("Reason", { exact: true }).fill("Accepted by the end-to-end test");
  await page.getByRole("button", { name: /^Accept scene/ }).click();
  await expect(page.getByText(/^Recorded:/)).toBeVisible();
  await page.goto(jobUrl);
  return true;
}

test("create a campaign, produce it in simulation, approve and deliver", async ({ page }) => {
  await page.addInitScript((name) => window.localStorage.setItem("vidsprod.operator.v1", name), OPERATOR);
  const name = `E2E family floater ${Date.now()}`;

  // 1. Create the campaign through the form.
  await page.goto("/campaigns/new");
  await expect(page.getByRole("note", { name: "Simulation" })).toContainText("nothing was sent to Google Vids");
  await page.getByLabel("Campaign name").fill(name);
  await page
    .getByRole("textbox", { name: "Brief", exact: true })
    .fill("A calm 30-second film introducing Prudential's family floater health plan to parents planning for their family's health. End by inviting them to talk to an advisor.");
  // An explicit call to action and a claim, given in the optional sections.
  await page.getByRole("button", { name: "More options" }).click();
  await page.getByLabel("Call to action").fill("Talk to a Prudential advisor today");
  await page.getByRole("button", { name: "Add a claim" }).click();
  await page.getByLabel("Approved fact").selectOption({ index: 1 });
  await page.getByRole("button", { name: "Create campaign" }).click();

  await expect(page).toHaveURL(/\/campaigns\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();

  // 2. Start production.
  await page.getByRole("button", { name: "Start production" }).click();
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/, { timeout: 30_000 });
  const jobUrl = page.url();

  // 3. The plan approval arrives over the live stream; approve it.
  await expect(page.locator('article[data-approval-kind="PLAN"]')).toBeVisible({ timeout: 120_000 });
  await expect(page.locator('li[data-stage="PLAN_APPROVAL"]')).toHaveAttribute("data-status", "WAITING_FOR_HUMAN");
  await decide(page, "PLAN", /Approve plan/);

  // 4. Wait for compliance approval (accepting any scene review on the way).
  const compliance = page.locator('article[data-approval-kind="COMPLIANCE"]');
  const deadline = Date.now() + 180_000;
  while (!(await compliance.isVisible())) {
    if (Date.now() > deadline) throw new Error("compliance approval did not appear");
    await acceptSceneReviews(page, jobUrl);
    const failed = await page.locator('[data-status="FAILED"]').first().isVisible();
    if (failed && (await page.getByText("The production failed").isVisible())) throw new Error("the production failed");
    await page.waitForTimeout(1000);
  }
  await decide(page, "COMPLIANCE", /Approve for delivery/);

  // 5. Completed, with every stage passed.
  await expect(page.getByRole("heading", { level: 1, name: "Completed" })).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText("16 of 16 stages passed")).toBeVisible();
  await expect(page.locator('li[data-stage="DELIVERY"]')).toHaveAttribute("data-status", /PASSED/);

  // 6. The video: a verified export, a stored delivery, a playable video.
  await page.getByRole("navigation", { name: "Production" }).getByRole("link", { name: "Video" }).click();
  await expect(page).toHaveURL(/\/delivery$/);
  await expect(page.locator('[data-status="VERIFIED"]').first()).toBeVisible();
  await page.getByText("Technical details").click();  // storage record and checks are folded away
  await expect(page.locator('[data-status="STORED"]').first()).toBeVisible();
  await expect(page.locator("video source")).toHaveAttribute("src", /\/api\/backend\/artifacts\/[0-9a-f-]{36}\/content/);
  await expect(page.getByRole("link", { name: "Download the video" })).toBeVisible();

  // The token never reaches the page.
  const html = await page.content();
  expect(html).not.toMatch(/Bearer /);
});
