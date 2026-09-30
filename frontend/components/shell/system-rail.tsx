"use client";

import Link from "next/link";
import { FlaskConical, ShieldOff, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { errorMessage } from "@/lib/api";
import { useBrowserStatus, useSystem } from "@/lib/hooks";
import { statusMeta } from "@/lib/status";
import type { SystemInfo } from "@/lib/types";
import { ToneIcon } from "../ui/status-badge";

export const MODE_LABELS: Record<string, string> = {
  mock: "Mock (simulated)",
  browser_test: "Browser test (read-only)",
  live: "Live",
};

export function simulationSentence(settings: { google_vids_mode?: string; ai_provider?: string } | undefined): string {
  const mockVids = settings?.google_vids_mode === "mock";
  const fakeAi = settings?.ai_provider === "fake";
  if (mockVids) {
    return fakeAi
      ? "Practice mode: Google Vids and the AI planner are simulated, and nothing was sent to Google Vids"
      : "Practice mode: Google Vids is simulated, and nothing was sent to Google Vids";
  }
  return "Practice mode: the AI planner is simulated, so these videos are not real production output";
}

/** Site-wide notices: practice mode, or the backend being unreachable. Nothing else. */
export function SiteNotices() {
  const { data: sys, error } = useSystem();
  if (error) {
    return (
      <div role="alert" className="border-b border-red-200 bg-red-50">
        <div className="mx-auto flex max-w-[1320px] items-center gap-2.5 px-4 py-2 text-[13px] font-medium text-red-deep sm:px-6 lg:px-8">
          <TriangleAlert aria-hidden className="size-4 shrink-0 text-red" />
          <span>The production server is not responding: {errorMessage(error)}</span>
        </div>
      </div>
    );
  }
  return sys?.simulation ? <SimulationBanner sentence={simulationSentence(sys.settings)} /> : null;
}

/** The persistent simulation banner. Also used on job pages when job.simulation is true. */
export function SimulationBanner({ sentence }: { sentence: string }) {
  return (
    <div role="note" aria-label="Simulation" className="border-b border-red-200 bg-red-50">
      <div className="mx-auto flex max-w-[1320px] items-center gap-2.5 px-4 py-2 text-[13px] font-medium text-red-deep sm:px-6 lg:px-8">
        <FlaskConical aria-hidden className="size-4 shrink-0 text-red" />
        <span>{sentence}</span>
      </div>
    </div>
  );
}

function RailItem({ label, children, href }: { label: string; children: ReactNode; href?: string }) {
  const body = (
    <>
      <span className="text-gray">{label}</span>
      <span className="flex items-center gap-1.5 font-medium text-ink">{children}</span>
    </>
  );
  return (
    <li className="flex items-center">
      {href ? (
        <Link href={href} className="flex items-center gap-2 rounded px-1 py-0.5 hover:bg-gray-100">
          {body}
        </Link>
      ) : (
        <span className="flex items-center gap-2 px-1 py-0.5">{body}</span>
      )}
    </li>
  );
}

export function budgetText(sys: SystemInfo | undefined) {
  if (!sys) return "—";
  return `${sys.budget.monthly_used} of ${sys.budget.monthly_limit} used`;
}

export function SystemRail() {
  const { data: sys, error: sysError } = useSystem();
  const { data: browser } = useBrowserStatus();

  if (sysError) {
    return (
      <div role="alert" className="border-b border-red-200 bg-red-50">
        <div className="mx-auto flex max-w-[1320px] items-center gap-2.5 px-4 py-2 text-[13px] font-medium text-red-deep sm:px-6 lg:px-8">
          <TriangleAlert aria-hidden className="size-4 shrink-0 text-red" />
          <span>Backend status unavailable: {errorMessage(sysError)}</span>
        </div>
      </div>
    );
  }

  const settings = sys?.settings;
  const mode = settings?.google_vids_mode;
  const session = browser?.last_check;
  const sessionMeta = statusMeta("session", session?.state);
  const chromeRunning = browser?.browser?.running;

  return (
    <>
      {sys?.simulation ? <SimulationBanner sentence={simulationSentence(settings)} /> : null}
      <div className="border-b border-gray-200 bg-gray-50">
        <ul
          aria-label="System status"
          className="mx-auto flex max-w-[1320px] flex-wrap items-center gap-x-5 gap-y-1 px-3 py-1.5 text-[12.5px] sm:px-5 lg:px-7"
        >
          <RailItem label="Mode" href="/browser">
            {mode ? MODE_LABELS[mode] ?? mode : "—"}
          </RailItem>
          <RailItem label="Live automation" href="/browser">
            {settings ? (
              settings.live_actions_allowed ? (
                <>
                  <ToneIcon tone="live" /> Allowed
                </>
              ) : (
                <>
                  <ShieldOff aria-hidden className="size-3.5 text-gray" /> Disabled
                </>
              )
            ) : (
              "—"
            )}
          </RailItem>
          <RailItem label="Chrome" href="/browser">
            {browser ? (
              mode === "mock" ? (
                "Not used in mock mode"
              ) : chromeRunning ? (
                <>
                  <ToneIcon tone="done" /> Connected
                </>
              ) : (
                <>
                  <ToneIcon tone="idle" /> Not running
                </>
              )
            ) : (
              "—"
            )}
          </RailItem>
          <RailItem label="Google Vids" href="/browser/login">
            {browser?.readiness ? (
              browser.readiness.label
            ) : browser?.mode === "mock" ? (
              "No real Google session (mock mode)"
            ) : browser ? (
              session ? (
                <>
                  <ToneIcon tone={sessionMeta.tone} /> {sessionMeta.label}
                </>
              ) : (
                "Not checked yet"
              )
            ) : (
              "—"
            )}
          </RailItem>
          <RailItem label="AI provider">{settings?.ai_provider ?? "—"}</RailItem>
          <RailItem label="Generations this month">{budgetText(sys)}</RailItem>
        </ul>
      </div>
    </>
  );
}
