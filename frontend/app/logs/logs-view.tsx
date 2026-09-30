"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/button";
import { SelectField, TextField } from "@/components/ui/field";
import { EmptyState, ErrorNotice, Loading } from "@/components/ui/notice";
import { PageHeader, Section } from "@/components/ui/section";
import { apiRequest } from "@/lib/api";
import { formatDateTime, shortId } from "@/lib/format";
import type { AIRequest, AuditEvent, JobSummary } from "@/lib/types";

const PAGE = 100;

function AuditTable({ rows, showJob }: { rows: AuditEvent[]; showJob: boolean }) {
  if (!rows.length) return <p className="px-5 py-4 text-[14px] text-gray">No events match.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-left text-[13px]">
        <caption className="visually-hidden">Audit events</caption>
        <thead className="bg-gray-50 text-gray">
          <tr>
            <th scope="col" className="px-5 py-2.5 font-medium">#</th>
            <th scope="col" className="px-5 py-2.5 font-medium">Time</th>
            <th scope="col" className="px-5 py-2.5 font-medium">Type</th>
            <th scope="col" className="px-5 py-2.5 font-medium">Message</th>
            <th scope="col" className="px-5 py-2.5 font-medium">Actor</th>
            {showJob ? <th scope="col" className="px-5 py-2.5 font-medium">Production</th> : null}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.map((e) => (
            <tr key={e.id} className="align-top">
              <td className="px-5 py-2 text-gray tabular">{e.id}</td>
              <td className="px-5 py-2 whitespace-nowrap text-gray tabular">{formatDateTime(e.created_at)}</td>
              <td className="px-5 py-2">
                <code className="text-ink">{e.type}</code>
              </td>
              <td className="px-5 py-2 break-words text-ink">{e.message}</td>
              <td className="px-5 py-2 text-ink">{e.actor}</td>
              {showJob ? (
                <td className="px-5 py-2">
                  {e.job_id ? (
                    <Link href={`/logs?job=${e.job_id}`} className="font-medium text-red-dark underline-offset-2 hover:underline">
                      {shortId(e.job_id)}
                    </Link>
                  ) : (
                    <span className="text-gray">—</span>
                  )}
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GlobalAudit({ prefix }: { prefix: string }) {
  const first = useSWR<AuditEvent[]>(`audit?limit=${PAGE}`, { refreshInterval: 10000 });
  const [older, setOlder] = useState<AuditEvent[]>([]);
  const [exhausted, setExhausted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const firstRows = first.data ?? [];
  const firstIds = new Set(firstRows.map((r) => r.id));
  const rows = [...firstRows, ...older.filter((r) => !firstIds.has(r.id))];
  const oldest = rows.length ? rows[rows.length - 1].id : null;

  const loadOlder = async () => {
    if (oldest == null) return;
    setLoading(true);
    setError(null);
    try {
      const page = await apiRequest<AuditEvent[]>(`audit?limit=${PAGE}&before=${oldest}`);
      setOlder((o) => [...o, ...page]);
      if (page.length < PAGE) setExhausted(true);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  };

  const filtered = prefix ? rows.filter((r) => r.type.startsWith(prefix)) : rows;
  return (
    <Section title="All activity" description="Newest first, across every campaign and production." className="overflow-hidden">
      <div className="-mx-5 -my-4">
        <ErrorNotice error={first.error} onRetry={() => first.mutate()} className="m-5" />
        {!first.data && !first.error ? (
          <div className="px-5 py-4">
            <Loading />
          </div>
        ) : null}
        {first.data ? <AuditTable rows={filtered} showJob /> : null}
        <div className="flex flex-wrap items-center gap-3 border-t border-gray-100 px-5 py-3">
          <p className="text-[13px] text-gray tabular">
            Showing {filtered.length} of {rows.length} loaded events{prefix ? ` matching “${prefix}”` : ""}.
          </p>
          {!exhausted && first.data && first.data.length >= PAGE ? (
            <Button size="sm" loading={loading} onClick={() => void loadOlder()}>
              Load older events
            </Button>
          ) : null}
          <ErrorNotice error={error} />
        </div>
      </div>
    </Section>
  );
}

function JobLogs({ jobId, prefix }: { jobId: string; prefix: string }) {
  const audit = useSWR<AuditEvent[]>(`jobs/${jobId}/audit?limit=1000`, { refreshInterval: 10000 });
  const ai = useSWR<AIRequest[]>(`jobs/${jobId}/ai-requests`, { refreshInterval: 15000 });
  const rows = [...(audit.data ?? [])].reverse();
  const filtered = prefix ? rows.filter((r) => r.type.startsWith(prefix)) : rows;
  const totals = (ai.data ?? []).reduce(
    (t, r) => ({ input: t.input + (r.input_tokens ?? 0), output: t.output + (r.output_tokens ?? 0), cost: t.cost + (r.cost_estimate ?? 0), costKnown: t.costKnown || r.cost_estimate != null }),
    { input: 0, output: 0, cost: 0, costKnown: false },
  );

  return (
    <div className="space-y-8">
      <Section
        title="AI requests"
        description={
          ai.data
            ? `${ai.data.length} requests, ${totals.input.toLocaleString("en-IN")} input and ${totals.output.toLocaleString("en-IN")} output tokens${totals.costKnown ? `, estimated cost ${totals.cost.toFixed(4)}` : ", no cost estimate recorded"}.`
            : undefined
        }
        className="overflow-hidden"
      >
        <div className="-mx-5 -my-4">
          <ErrorNotice error={ai.error} onRetry={() => ai.mutate()} className="m-5" />
          {!ai.data && !ai.error ? (
            <div className="px-5 py-4">
              <Loading />
            </div>
          ) : null}
          {ai.data && ai.data.length === 0 ? <p className="px-5 py-4 text-[14px] text-gray">No AI requests recorded.</p> : null}
          {ai.data?.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-left text-[13px]">
                <caption className="visually-hidden">AI requests</caption>
                <thead className="bg-gray-50 text-gray">
                  <tr>
                    {["Time", "Purpose", "Provider", "Model", "Status", "Attempts", "Latency", "Tokens in / out", "Cost"].map((h) => (
                      <th key={h} scope="col" className="px-5 py-2.5 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {ai.data.map((r) => (
                    <tr key={r.id} className="align-top">
                      <td className="px-5 py-2 whitespace-nowrap text-gray tabular">{formatDateTime(r.created_at)}</td>
                      <td className="px-5 py-2 text-ink">{r.purpose}</td>
                      <td className="px-5 py-2 text-ink">{r.provider}</td>
                      <td className="px-5 py-2 text-ink">{r.model}</td>
                      <td className="px-5 py-2">
                        <span className={r.status === "OK" ? "text-ink" : "font-medium text-red-dark"}>{r.status}</span>
                        {r.error ? <span className="block text-[12px] text-red-dark">{r.error}</span> : null}
                      </td>
                      <td className="px-5 py-2 tabular">{r.attempts}</td>
                      <td className="px-5 py-2 tabular">{r.latency_ms != null ? `${r.latency_ms} ms` : "—"}</td>
                      <td className="px-5 py-2 tabular">
                        {r.input_tokens ?? "—"} / {r.output_tokens ?? "—"}
                      </td>
                      <td className="px-5 py-2 tabular">{r.cost_estimate != null ? r.cost_estimate.toFixed(4) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      </Section>

      <Section title="Production activity" description="Newest first." className="overflow-hidden">
        <div className="-mx-5 -my-4">
          <ErrorNotice error={audit.error} onRetry={() => audit.mutate()} className="m-5" />
          {!audit.data && !audit.error ? (
            <div className="px-5 py-4">
              <Loading />
            </div>
          ) : null}
          {audit.data ? <AuditTable rows={filtered} showJob={false} /> : null}
        </div>
      </Section>
    </div>
  );
}

export function LogsPage() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const jobId = params.get("job") ?? "";
  const [prefix, setPrefix] = useState("");
  const jobs = useSWR<JobSummary[]>("jobs?limit=100");

  const setJob = (id: string) => {
    const next = new URLSearchParams(params.toString());
    if (id) next.set("job", id);
    else next.delete("job");
    router.replace(`${pathname}${next.toString() ? `?${next}` : ""}`);
  };

  const selected = jobs.data?.find((j) => j.id === jobId);

  return (
    <>
      <PageHeader
        meta={
          <Link href="/advanced" className="font-medium text-red-dark underline-offset-2 hover:underline">
            Advanced
          </Link>
        }
        title="Logs"
        lead="Everything the automation and its operators recorded."
      />
      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:max-w-3xl">
        <SelectField label="Production" value={jobId} onChange={(e) => setJob(e.target.value)}>
          <option value="">All productions</option>
          {jobId && !selected ? <option value={jobId}>Production {shortId(jobId)}</option> : null}
          {(jobs.data ?? []).map((j) => (
            <option key={j.id} value={j.id}>
              {j.campaign_name ?? "Production"}, {formatDateTime(j.created_at)} ({j.status.toLowerCase()})
            </option>
          ))}
        </SelectField>
        <TextField label="Event type starts with" placeholder="for example stage. or approval." value={prefix} onChange={(e) => setPrefix(e.target.value.trim())} />
      </div>
      {jobId ? (
        <>
          <p className="mb-4 text-[14px]">
            <Link href={`/jobs/${jobId}`} className="font-medium text-red-dark underline-offset-2 hover:underline">
              Open this production&apos;s monitor
            </Link>
          </p>
          <JobLogs jobId={jobId} prefix={prefix} />
        </>
      ) : jobs.data && jobs.data.length === 0 ? (
        <>
          <EmptyState title="No productions yet">Per-production logs and AI requests appear once a production has run.</EmptyState>
          <div className="mt-8">
            <GlobalAudit prefix={prefix} />
          </div>
        </>
      ) : (
        <GlobalAudit prefix={prefix} />
      )}
    </>
  );
}
