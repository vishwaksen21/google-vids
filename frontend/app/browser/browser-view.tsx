"use client";

import Link from "next/link";
import { CircleCheck, CircleDashed, CircleX, RefreshCw, X } from "lucide-react";
import { Button, LinkButton } from "@/components/ui/button";
import { ErrorNotice, Loading, Notice } from "@/components/ui/notice";
import { PageHeader, Section } from "@/components/ui/section";
import { apiRequest } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useAction, useBrowserStatus, useSystem } from "@/lib/hooks";

const CONNECTED = new Set(["READY", "SIGNED_IN"]);

/** The Google account the automation uses, in plain words. Technical detail lives under Advanced. */
export function BrowserPage() {
  const status = useBrowserStatus({ refreshInterval: 10000 });
  const sys = useSystem();
  const b = status.data;
  const mock = sys.data?.settings.google_vids_mode === "mock";
  const check = b?.last_check;
  const connected = !!check && CONNECTED.has(check.state);
  const running = !!b?.browser?.running;

  const release = useAction(async () => {
    const data = await apiRequest<Record<string, unknown>>("browser/release-clip", { method: "POST" });
    await status.mutate();
    return data;
  });

  const act = useAction(async (action: "check" | "close") => {
    const data = await apiRequest<Record<string, unknown>>(`browser/${action}`, { method: "POST" });
    await status.mutate();
    return data;
  });

  return (
    <>
      <PageHeader title="Google account" lead="The Google account the automation signs in with to make videos in Google Vids." />

      {b?.unsaved_clip ? (
        <div role="alert" className="mb-6 rounded-lg border border-red bg-white p-5 text-[14px] text-ink">
          <p className="font-semibold text-red-dark">A generated clip is not inserted yet</p>
          <p className="mt-1">
            Scene {b.unsaved_clip.scene_key}, take {b.unsaved_clip.take_no} is open in the automation&apos;s Chrome window but could not be confirmed
            in the timeline. Insert it in that window. Do not close Chrome or reload the video: the clip exists only in that page.
          </p>
          <p className="mt-2 text-gray">
            Once it is in the timeline (or if that window is already closed and the clip is lost), continue so productions can run again.
          </p>
          <Button className="mt-3" variant="primary" loading={release.pending} onClick={() => void release.run()}>
            It&apos;s inserted (or lost) — continue
          </Button>
          <ErrorNotice error={release.error} className="mt-3" />
        </div>
      ) : null}

      <ErrorNotice error={status.error} onRetry={() => status.mutate()} className="mb-6" />
      {!b && !status.error ? <Loading label="Checking the connection" lines={3} /> : null}

      {b ? (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <Section title="Connection">
            {mock ? (
              <StatusLine icon={<CircleDashed aria-hidden className="size-6 text-gray" />} title="Practice mode">
                Google Vids is simulated, so no Google account is used. Videos made now are practice runs.
              </StatusLine>
            ) : connected ? (
              <StatusLine icon={<CircleCheck aria-hidden className="size-6 text-red" />} title="Connected">
                Signed in as <strong className="font-semibold">{check?.account ?? "the production account"}</strong>
                {check?.checked_at ? <>, checked {formatDateTime(check.checked_at)}</> : null}.
              </StatusLine>
            ) : check ? (
              <StatusLine icon={<CircleX aria-hidden className="size-6 text-red-dark" />} title="Not connected">
                {check.message ?? "The automation is not signed in to Google Vids."}{" "}
                <Link href="/browser/login" className="font-medium text-red-dark underline-offset-2 hover:underline">
                  How to sign in
                </Link>
              </StatusLine>
            ) : (
              <StatusLine icon={<CircleDashed aria-hidden className="size-6 text-gray" />} title="Not checked yet">
                Press <em>Check connection</em> to see which account is signed in.
              </StatusLine>
            )}

            {!mock ? (
              <div className="mt-5 flex flex-wrap gap-2">
                <Button
                  variant="primary"
                  loading={act.pending}
                  disabled={act.pending}
                  icon={<RefreshCw aria-hidden className="size-4" />}
                  onClick={() => void act.run("check")}
                >
                  Check connection
                </Button>
                {running ? (
                  <Button disabled={act.pending} icon={<X aria-hidden className="size-4" />} onClick={() => void act.run("close")}>
                    Close the automation window
                  </Button>
                ) : null}
                <LinkButton href="/browser/login" variant="tertiary">
                  Sign-in help
                </LinkButton>
              </div>
            ) : null}
            <ErrorNotice error={act.error} className="mt-4" />
            {!mock ? (
              <p className="mt-4 text-[13px] text-gray">
                Checking opens a separate Chrome window. To sign in or switch accounts yourself, close it first.
              </p>
            ) : null}
          </Section>

          <Section title="Making real videos">
            {b.live_actions_allowed ? (
              <Notice tone="attention" title="Real generation is on">
                Each generated scene uses one Google Vids generation from this account&apos;s monthly limit.
              </Notice>
            ) : (
              <p className="text-[15px] leading-relaxed text-ink">
                Real generation is <strong className="font-semibold">switched off</strong>, so nothing spends this account&apos;s Google Vids
                generations. It is turned on in the backend configuration once a production is approved.
              </p>
            )}
            <p className="mt-4 text-[13px] text-gray">
              Technical detail, such as the automation&apos;s mode and which Google Vids actions are verified, is under{" "}
              <Link href="/advanced/automation" className="font-medium text-red-dark underline-offset-2 hover:underline">
                Advanced → Automation details
              </Link>
              .
            </p>
          </Section>
        </div>
      ) : null}
    </>
  );
}

function StatusLine({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3">
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0">
        <p className="text-[18px] font-semibold text-ink">{title}</p>
        <p className="mt-1 text-[15px] leading-relaxed text-ink">{children}</p>
      </div>
    </div>
  );
}
