"use client";

import Link from "next/link";
import { useBrowserStatus, useSystem } from "@/lib/hooks";
import { formatDateTime } from "@/lib/format";
import { statusMeta } from "@/lib/status";
import { ProgressBar } from "./job-row";
import { MODE_LABELS } from "./shell/system-rail";
import { ErrorNotice, Loading } from "./ui/notice";
import { Section } from "./ui/section";
import { StatusBadge, ToneIcon } from "./ui/status-badge";

function Row({ label, children, sub }: { label: string; children: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 py-3 first:pt-0 last:pb-0">
      <dt className="text-[13px] text-gray">{label}</dt>
      <dd className="text-[14px] text-ink">
        <div className="flex flex-wrap items-center gap-2">{children}</div>
        {sub ? <div className="mt-1 text-[13px] text-gray">{sub}</div> : null}
      </dd>
    </div>
  );
}

export function SystemStatusPanel() {
  const sys = useSystem();
  const browser = useBrowserStatus();
  const s = sys.data;
  const b = browser.data;
  const settings = s?.settings;
  const session = b?.last_check;

  return (
    <Section title="System" description="What the automation is connected to right now.">
      {sys.error ? <ErrorNotice error={sys.error} onRetry={() => sys.mutate()} className="mb-4" /> : null}
      {!s && !sys.error ? (
        <Loading label="Reading system status" />
      ) : s ? (
        <dl className="divide-y divide-gray-100">
          <Row
            label="Execution mode"
            sub={settings?.google_vids_mode === "mock" ? "Google Vids is simulated; nothing is sent to Google." : undefined}
          >
            <span className="font-medium">{settings ? MODE_LABELS[settings.google_vids_mode] ?? settings.google_vids_mode : "—"}</span>
          </Row>
          <Row label="Live actions">
            {settings?.live_actions_allowed ? (
              <StatusBadge kind="stage" value="RUNNING" label="Allowed" size="sm" />
            ) : (
              <StatusBadge kind="stage" value="SKIPPED" label="Live automation disabled" size="sm" />
            )}
          </Row>
          <Row
            label="Chrome"
            sub={b?.browser?.last_error ? <span className="text-red-dark">Last error: {String(b.browser.last_error)}</span> : undefined}
          >
            {b ? (
              b.mode === "mock" ? (
                <span>Not used in mock mode</span>
              ) : b.browser?.running ? (
                <>
                  <ToneIcon tone="done" /> Connected{b.browser.busy ? ", busy" : ""}
                </>
              ) : (
                <>
                  <ToneIcon tone="idle" /> Not running
                </>
              )
            ) : browser.error ? (
              <span className="text-red-dark">Unavailable</span>
            ) : (
              "—"
            )}
            <Link href="/browser" className="text-[13px] font-medium text-red-dark underline-offset-2 hover:underline">
              Browser
            </Link>
          </Row>
          <Row
            label="Google Vids sign-in"
            sub={session ? `${session.message ?? ""}${session.checked_at ? ` (checked ${formatDateTime(session.checked_at)})` : ""}` : "No session check has been recorded."}
          >
            {b?.mode === "mock" ? (
              <span className="text-gray">No real Google session (mock mode)</span>
            ) : session ? (
              <StatusBadge kind="session" value={session.state} size="sm" />
            ) : (
              <span className="text-gray">Not checked</span>
            )}
            {b?.mode !== "mock" && session?.account ? <span className="text-[13px] text-gray">{session.account}</span> : null}
            <Link href="/browser/login" className="text-[13px] font-medium text-red-dark underline-offset-2 hover:underline">
              Sign-in
            </Link>
          </Row>
          <Row
            label="AI provider"
            sub={s.provider_error ? <span className="text-red-dark">{s.provider_error}</span> : undefined}
          >
            <span className="font-medium">{settings?.ai_provider}</span>
            {settings?.ai_provider === "fake" ? <span className="text-[13px] text-gray">(simulated responses)</span> : null}
          </Row>
          <Row label={`Generation budget, ${s.budget.period}`}>
            <div className="w-full">
              <p className="mb-1.5 tabular">
                <span className="font-semibold">{s.budget.monthly_used}</span> of {s.budget.monthly_limit} generations used
              </p>
              <ProgressBar value={s.budget.monthly_limit ? s.budget.monthly_used / s.budget.monthly_limit : 0} label="Monthly generation budget used" />
            </div>
          </Row>
        </dl>
      ) : null}
    </Section>
  );
}

export function sessionLabel(state: string | null | undefined) {
  return statusMeta("session", state).label;
}

/** The Home page's plain-language view of the automation: connected or not, real or practice. */
export function GoogleVidsCard() {
  const sys = useSystem();
  const browser = useBrowserStatus();
  const settings = sys.data?.settings;
  const check = browser.data?.last_check;
  const mock = settings?.google_vids_mode === "mock";
  const connected = check?.state === "READY" || check?.state === "SIGNED_IN";

  return (
    <Section title="Google Vids">
      {sys.error ? <ErrorNotice error={sys.error} onRetry={() => sys.mutate()} className="mb-4" /> : null}
      {!sys.data && !sys.error ? (
        <Loading label="Checking Google Vids" />
      ) : (
        <div className="space-y-3 text-[15px] leading-relaxed text-ink">
          {mock ? (
            <p>
              <strong className="font-semibold">Practice mode.</strong> Google Vids is simulated; no real videos are made.
            </p>
          ) : connected ? (
            <p>
              <strong className="font-semibold">Connected</strong> as {check?.account ?? "the production account"}.
            </p>
          ) : (
            <p>
              <strong className="font-semibold">Not connected.</strong> Check the Google account before making real videos.
            </p>
          )}
          {!mock ? (
            <p className="text-[14px] text-gray">
              Real generation is {settings?.live_actions_allowed ? "on" : "switched off"}
              {settings?.live_actions_allowed ? ": each generated scene uses one of the account's monthly generations." : "."}
            </p>
          ) : null}
          <Link href="/browser" className="inline-block text-[14px] font-medium text-red-dark underline-offset-2 hover:underline">
            Google account
          </Link>
        </div>
      )}
    </Section>
  );
}
