"use client";

import Link from "next/link";
import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { ArtifactView } from "@/components/artifact";
import { Button } from "@/components/ui/button";
import { ErrorNotice, Loading, Notice } from "@/components/ui/notice";
import { KeyValues, PageHeader, Section } from "@/components/ui/section";
import { StatusBadge } from "@/components/ui/status-badge";
import { apiRequest } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useAction, useBrowserStatus } from "@/lib/hooks";
import type { SessionCheck } from "@/lib/types";

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="grid grid-cols-[40px_minmax(0,1fr)] gap-4 border-b border-gray-100 py-5 last:border-b-0">
      <span aria-hidden className="flex size-9 items-center justify-center rounded-full border border-gray-300 text-[15px] font-semibold text-ink tabular">
        {n}
      </span>
      <div>
        <h3 className="text-[16px] font-semibold text-ink">
          <span className="visually-hidden">Step {n}: </span>
          {title}
        </h3>
        <div className="mt-1 space-y-3 text-[14px] text-gray-700">{children}</div>
      </div>
    </li>
  );
}

export function LoginPage() {
  const status = useBrowserStatus({ refreshInterval: 10000 });
  const [opened, setOpened] = useState<SessionCheck | null>(null);
  const [completed, setCompleted] = useState<SessionCheck | null>(null);

  const open = useAction(async () => {
    setCompleted(null);
    const r = await apiRequest<SessionCheck>("browser/login", { method: "POST" });
    setOpened(r);
    void status.mutate();
    return r;
  });
  const complete = useAction(async () => {
    const r = await apiRequest<SessionCheck>("browser/login/complete", { method: "POST" });
    setCompleted(r);
    void status.mutate();
    return r;
  });

  const last = status.data?.last_check;
  const signedIn = completed && (completed.state === "SIGNED_IN" || completed.state === "READY");

  return (
    <>
      <PageHeader title="Google account sign-in" lead="Google Vids runs in a dedicated Chrome window signed in to the production Google account." />

      <Notice tone="info" className="mb-8" title="This app never asks for, sees or stores your Google password">
        You sign in by hand, in the Chrome window the automation uses. The dashboard only asks the backend to open that window and then to check the result.
      </Notice>

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_400px]">
        <Section title="Sign in">
          {status.data?.mode === "mock" ? (
            <Notice tone="attention" className="mb-2" title="The backend is in mock mode">
              There is no real browser session, so these steps are refused with an explanation. Switch the backend to browser test mode to sign in. See{" "}
              <Link href="/browser" className="font-medium text-red-dark underline-offset-2 hover:underline">
                Browser
              </Link>
              .
            </Notice>
          ) : null}
          <ol>
            <Step n={1} title="Open the sign-in window">
              <p>Opens Google Vids in the dedicated Chrome window on the computer running the backend.</p>
              <Button variant="primary" loading={open.pending} onClick={() => void open.run()}>
                Open sign-in window
              </Button>
              <ErrorNotice error={open.error} />
              {opened ? (
                <p role="status" className="text-ink">
                  The window reports: {opened.message ?? opened.state}
                </p>
              ) : null}
            </Step>
            <Step n={2} title="Sign in by hand in that Chrome window">
              <p>
                Switch to the Chrome window, choose the production account and complete any security check Google shows. Do not type the password anywhere in this dashboard; there is nowhere to
                type it.
              </p>
            </Step>
            <Step n={3} title="Confirm you have signed in">
              <p>The backend checks the session. If it holds, productions waiting for sign-in resume.</p>
              <Button loading={complete.pending} onClick={() => void complete.run()}>
                I&apos;ve signed in
              </Button>
              <ErrorNotice error={complete.error} />
              {completed ? (
                <Notice tone={signedIn ? "info" : "error"} title={`Session check: ${completed.state}`}>
                  <p>{completed.message}</p>
                  {completed.resumed_jobs?.length ? (
                    <ul className="mt-2 list-disc pl-5">
                      {completed.resumed_jobs.map((id) => (
                        <li key={id}>
                          <Link href={`/jobs/${id}`} className="font-medium text-red-dark underline-offset-2 hover:underline">
                            Resumed production {id.slice(-8)}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-1">No production was waiting for sign-in.</p>
                  )}
                </Notice>
              ) : null}
            </Step>
          </ol>
        </Section>

        <Section title="Last session check">
          {status.error ? <ErrorNotice error={status.error} onRetry={() => status.mutate()} /> : null}
          {!status.data && !status.error ? <Loading /> : null}
          {status.data?.mode === "mock" ? (
            <p className="text-gray">
              Mock mode: there is no real Google session. Nothing here reflects Google sign-in.
            </p>
          ) : status.data ? (
            last ? (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge kind="session" value={last.state} />
                </div>
                <KeyValues
                  columns={1}
                  items={[
                    { label: "Account", value: last.account ?? "Not identified" },
                    { label: "Message", value: last.message ?? "—" },
                    { label: "Page", value: last.page ?? "—" },
                    { label: "Checked", value: formatDateTime(last.checked_at) },
                  ]}
                />
                {last.evidence_artifact_id ? <ArtifactView id={last.evidence_artifact_id} alt="Screenshot from the last session check" /> : <p className="text-[13px] text-gray">No screenshot was captured.</p>}
              </div>
            ) : (
              <p className="text-[14px] text-gray">No session check has been recorded yet.</p>
            )
          ) : null}
          <p className="mt-5 flex items-start gap-2 text-[13px] text-gray">
            <ShieldCheck aria-hidden className="mt-0.5 size-4 shrink-0" />
            Accounts are shown masked. Only the account configured on the backend is accepted.
          </p>
        </Section>
      </div>
    </>
  );
}
