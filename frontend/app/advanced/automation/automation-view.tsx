"use client";

import Link from "next/link";
import { useState } from "react";
import { ArtifactView } from "@/components/artifact";
import { Button } from "@/components/ui/button";
import { ErrorNotice, Loading, Notice } from "@/components/ui/notice";
import { KeyValues, PageHeader, Section } from "@/components/ui/section";
import { StatusBadge } from "@/components/ui/status-badge";
import { apiRequest } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useAction, useBrowserStatus, useSystem } from "@/lib/hooks";
import type { SessionCheck } from "@/lib/types";

const MODES = [
  {
    key: "mock",
    name: "Mock",
    body: "Google Vids is simulated by a mock adapter. No browser opens and nothing is sent to Google. Use it to rehearse the whole pipeline.",
  },
  {
    key: "browser_test",
    name: "Browser test",
    body: "A real Chrome window opens Google Vids, read-only: session checks and diagnostics. Nothing is generated and no generation is spent.",
  },
  {
    key: "live",
    name: "Live",
    body: "Real generation in Google Vids, spending real monthly quota. Only allowed when GOOGLE_VIDS_LIVE_AUTOMATION_ENABLED=true is set on the backend after authorisation.",
  },
];

interface Diagnosis {
  page: string;
  title: string;
  controls: number;
  candidate_locators: { strategy: string; role: string; value: string; exact: boolean; matches: number; usable: boolean }[];
  note: string;
  screenshot_artifact_id: string;
  aria_snapshot_artifact_id: string;
}

type Result = { kind: "launch" | "close"; data: Record<string, unknown> } | { kind: "check"; data: SessionCheck } | { kind: "diagnose"; data: Diagnosis };

export function AutomationPage() {
  const status = useBrowserStatus({ refreshInterval: 10000 });
  const sys = useSystem();
  const [result, setResult] = useState<Result | null>(null);
  const [lastAction, setLastAction] = useState<string>("");

  const act = useAction(async (action: "launch" | "check" | "diagnose" | "close") => {
    setResult(null);
    setLastAction(action);
    const data = await apiRequest<Record<string, unknown>>(`browser/${action}`, { method: "POST" });
    setResult({ kind: action, data } as Result);
    void status.mutate();
    return data;
  });

  const b = status.data;
  const browser = b?.browser;

  return (
    <>
      <PageHeader
        meta={
          <Link href="/advanced" className="font-medium text-red-dark underline-offset-2 hover:underline">
            Advanced
          </Link>
        }
        title="Automation details"
        lead="The Chrome window the automation uses, what it is allowed to do in Google Vids, and the evidence behind each capability."
        actions={
          <Link href="/browser/login" className="text-[14px] font-medium text-red-dark underline-offset-2 hover:underline">
            Google sign-in
          </Link>
        }
      />

      {b?.unsaved_clip ? (
        <div role="alert" className="mb-6 rounded-lg border border-red bg-white p-5 text-[14px] text-ink">
          <p className="font-semibold text-red-dark">A generated clip is not inserted yet</p>
          <p className="mt-1">
            Scene {b.unsaved_clip.scene_key}, take {b.unsaved_clip.take_no} is open in the automation&apos;s Chrome window
            but could not be confirmed in the timeline. Insert it in that window. Do not close Chrome or reload the Vid:
            the clip exists only in that page.
          </p>
        </div>
      ) : null}

      {b?.readiness ? (
        <section aria-labelledby="readiness-heading" className="mb-8 rounded-lg border border-line bg-white p-5">
          <h2 id="readiness-heading" className="text-[18px] font-semibold text-ink">
            Real Google Vids readiness: {b.readiness.label}
          </h2>
          {b.readiness.steps.length ? (
            <ul className="mt-3 space-y-1.5 text-[14px]">
              {b.readiness.steps.map((step) => (
                <li key={step.key} className="flex gap-2">
                  <span aria-hidden="true" className={step.ok ? "text-ink" : "text-red-dark"}>{step.ok ? "✓" : "✗"}</span>
                  <span className={step.ok ? "text-ink" : "text-gray"}>{step.label}</span>
                  <span className="sr-only">{step.ok ? "done" : "not done"}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {b.readiness.blockers.length ? (
            <p className="mt-3 text-[14px] text-gray">Blocked by: {b.readiness.blockers.join("; ")}</p>
          ) : null}
        </section>
      ) : null}

      <section aria-labelledby="modes-heading" className="mb-8">
        <h2 id="modes-heading" className="mb-3 text-[18px] font-semibold text-ink">
          Execution mode
        </h2>
        <ul className="grid gap-4 md:grid-cols-3">
          {MODES.map((m) => {
            const current = b?.mode === m.key;
            return (
              <li key={m.key} className={`relative rounded-xl border p-5 ${current ? "border-red bg-white" : "border-gray-200 bg-gray-50"}`} aria-current={current ? "true" : undefined}>
                {current ? <span aria-hidden className="absolute inset-x-0 top-0 h-[3px] rounded-t-xl bg-red" /> : null}
                <p className="flex items-center justify-between gap-2">
                  <span className={`text-[16px] font-semibold ${current ? "text-red-dark" : "text-ink"}`}>{m.name}</span>
                  {current ? <span className="text-[12.5px] font-medium text-red-dark">Current mode</span> : null}
                </p>
                <p className="mt-2 text-[14px] leading-relaxed text-gray-700">{m.body}</p>
              </li>
            );
          })}
        </ul>
        <p className="mt-3 text-[13px] text-gray">The mode is set on the backend (GOOGLE_VIDS_MODE). It cannot be changed from the dashboard.</p>
      </section>

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Section title="Status">
          {status.error ? <ErrorNotice error={status.error} onRetry={() => status.mutate()} /> : null}
          {!b && !status.error ? <Loading /> : null}
          {b ? (
            <div className="space-y-5">
              <div className="flex flex-wrap gap-2">
                {b.live_actions_allowed ? (
                  <StatusBadge kind="stage" value="RUNNING" label="Live actions allowed" />
                ) : (
                  <StatusBadge kind="stage" value="SKIPPED" label="Live automation disabled" />
                )}
                {b.simulated ? <StatusBadge kind="stage" value="SKIPPED" label="Simulated adapter" /> : null}
              </div>
              <KeyValues
                items={[
                  { label: "Live automation flag", value: b.live_automation_flag ? "Enabled" : "Not enabled" },
                  { label: "Allowed Google account", value: b.allowed_account_configured ? "Configured" : "Not configured" },
                  { label: "Chrome", value: b.mode === "mock" ? "Not used in mock mode" : browser?.running ? "Running" : "Not running" },
                  { label: "Current page", value: browser?.page ? String(browser.page) : "—" },
                  { label: "Busy", value: browser?.busy ? "Yes, an action is in progress" : "No" },
                  { label: "Launched", value: browser?.launched_at ? formatDateTime(String(browser.launched_at)) : "—" },
                  { label: "Channel", value: browser?.channel ? String(browser.channel) : "—" },
                  { label: "Headless", value: browser?.headless == null ? "—" : browser.headless ? "Yes" : "No" },
                ]}
              />
              {browser?.last_error ? <Notice tone="error" title="Last browser error">{String(browser.last_error)}</Notice> : null}
            </div>
          ) : null}
        </Section>

        <Section title="Actions" description="Every action is read-only unless the mode is live.">
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" loading={act.pending && lastAction === "launch"} disabled={act.pending} onClick={() => void act.run("launch")}>
              Launch Chrome
            </Button>
            <Button loading={act.pending && lastAction === "check"} disabled={act.pending} onClick={() => void act.run("check")}>
              Check session
            </Button>
            <Button loading={act.pending && lastAction === "diagnose"} disabled={act.pending} onClick={() => void act.run("diagnose")}>
              Run diagnostics
            </Button>
            <Button loading={act.pending && lastAction === "close"} disabled={act.pending} onClick={() => void act.run("close")}>
              Close browser
            </Button>
          </div>
          {b?.mode === "mock" ? (
            <p className="mt-3 text-[13px] text-gray">In mock mode Launch, Diagnostics and Close are refused by the backend, which explains why. Check session returns the simulated session.</p>
          ) : null}
          <div className="mt-4 space-y-4" aria-live="polite">
            <ErrorNotice error={act.error} title="The backend refused the action" />
            {result?.kind === "check" ? (
              <Notice title={`Session: ${result.data.state}`}>
                {result.data.message}
                {result.data.account ? ` (${result.data.account})` : ""}
              </Notice>
            ) : null}
            {result?.kind === "launch" || result?.kind === "close" ? (
              <Notice title={result.kind === "launch" ? "Chrome reported running" : "Chrome closed"}>
                {result.data.running ? `Running, page ${String(result.data.page ?? "none")}` : "Not running"}
              </Notice>
            ) : null}
          </div>
        </Section>
      </div>

      {result?.kind === "check" && result.data.evidence_artifact_id ? (
        <Section title="Session evidence" className="mt-8">
          <ArtifactView id={result.data.evidence_artifact_id} alt="Screenshot from the session check" />
        </Section>
      ) : null}

      {result?.kind === "diagnose" ? (
        <Section title="Diagnostics" description={`${result.data.controls} controls on ${result.data.page} (${result.data.title})`} className="mt-8">
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <ArtifactView id={result.data.screenshot_artifact_id} alt="Screenshot of the Google Vids page" />
            <div>
              <p className="mb-2 text-[13px] text-gray">{result.data.note}</p>
              <div className="max-h-[480px] overflow-auto rounded-lg border border-gray-200">
                <table className="w-full text-left text-[13px]">
                  <caption className="visually-hidden">Candidate locators</caption>
                  <thead className="sticky top-0 bg-gray-50 text-gray">
                    <tr>
                      <th scope="col" className="px-3 py-2 font-medium">Role</th>
                      <th scope="col" className="px-3 py-2 font-medium">Name</th>
                      <th scope="col" className="px-3 py-2 font-medium">Matches</th>
                      <th scope="col" className="px-3 py-2 font-medium">Usable</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {result.data.candidate_locators.map((l, i) => (
                      <tr key={i}>
                        <td className="px-3 py-1.5">{l.role}</td>
                        <td className="px-3 py-1.5 break-words">{l.value}</td>
                        <td className="px-3 py-1.5 tabular">{l.matches}</td>
                        <td className="px-3 py-1.5">{l.usable ? "Unique" : "Ambiguous"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <a className="mt-2 inline-block text-[13px] font-medium text-red-dark underline-offset-2 hover:underline" href={`/api/backend/artifacts/${result.data.aria_snapshot_artifact_id}/content`} target="_blank" rel="noreferrer">
                Accessibility snapshot
              </a>
            </div>
          </div>
        </Section>
      ) : null}

      <Section
        title="Capability registry"
        description={
          sys.data?.capabilities_simulated
            ? "What the automation may do in Google Vids. These entries come from the mock adapter and are simulated."
            : "What the automation may do in Google Vids. Only verified capabilities are used."
        }
        className="mt-8 overflow-hidden"
      >
        <ErrorNotice error={sys.error} onRetry={() => sys.mutate()} />
        {!sys.data && !sys.error ? <Loading /> : null}
        {sys.data ? (
          <div className="-mx-5 -my-4 overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-[14px]">
              <caption className="visually-hidden">Google Vids capabilities</caption>
              <thead className="bg-gray-50 text-[13px] text-gray">
                <tr>
                  <th scope="col" className="px-5 py-2.5 font-medium">Capability</th>
                  <th scope="col" className="px-5 py-2.5 font-medium">Status</th>
                  <th scope="col" className="px-5 py-2.5 font-medium">Source</th>
                  <th scope="col" className="px-5 py-2.5 font-medium">Notes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {sys.data.capabilities.map((c) => (
                  <tr key={c.key} className="align-top">
                    <td className="px-5 py-3">
                      <code className="text-[13px] font-semibold text-ink">{c.key}</code>
                      <span className="block text-[13px] text-gray">{c.description}</span>
                    </td>
                    <td className="px-5 py-3">
                      <StatusBadge kind="capability" value={c.status} size="sm" />
                    </td>
                    <td className="px-5 py-3 text-ink">
                      {c.source}
                      {c.verified_on ? <span className="block text-[12.5px] text-gray">verified {c.verified_on}</span> : null}
                    </td>
                    <td className="px-5 py-3 text-[13px] text-gray-700">
                      {c.notes ?? "—"}
                      {c.reference ? (
                        <a href={c.reference} target="_blank" rel="noreferrer" className="mt-1 block font-medium text-red-dark underline-offset-2 hover:underline">
                          Google documentation<span className="visually-hidden"> for {c.key}</span>
                        </a>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Section>
    </>
  );
}
