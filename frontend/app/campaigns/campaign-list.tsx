"use client";

import Link from "next/link";
import { Plus } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { LinkButton } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/field";
import { EmptyState, ErrorNotice, Loading } from "@/components/ui/notice";
import { PageHeader } from "@/components/ui/section";
import { StatusBadge } from "@/components/ui/status-badge";
import { formatDateTime } from "@/lib/format";
import { humanise } from "@/lib/status";
import type { Campaign } from "@/lib/types";

export function CampaignList() {
  const [archived, setArchived] = useState(false);
  const { data, error, mutate } = useSWR<Campaign[]>(`campaigns${archived ? "?include_archived=true" : ""}`);

  return (
    <>
      <PageHeader
        title="Campaigns"
        lead="Each campaign is one video idea. Open one to make a video from it."
        actions={
          <LinkButton href="/campaigns/new" variant="primary" icon={<Plus aria-hidden className="size-4" />}>
            New campaign
          </LinkButton>
        }
      />
      <div className="mb-4 flex justify-end">
        <Checkbox label="Show archived campaigns" checked={archived} onChange={(e) => setArchived(e.target.checked)} />
      </div>
      <ErrorNotice error={error} onRetry={() => mutate()} className="mb-4" />
      {!data && !error ? <Loading label="Loading campaigns" /> : null}
      {data && data.length === 0 ? (
        <EmptyState title="No campaigns yet" action={<LinkButton href="/campaigns/new" variant="primary">New campaign</LinkButton>}>
          Create a campaign with a brief, then start a production from it.
        </EmptyState>
      ) : null}
      {data && data.length > 0 ? (
        <div className="overflow-x-auto rounded-xl border border-gray-200">
          <table className="w-full min-w-[720px] text-left text-[14px]">
            <caption className="visually-hidden">Campaigns</caption>
            <thead className="bg-gray-50 text-[13px] text-gray">
              <tr>
                <th scope="col" className="px-5 py-3 font-medium">Campaign</th>
                <th scope="col" className="px-5 py-3 font-medium">Campaign status</th>
                <th scope="col" className="px-5 py-3 font-medium">Brief</th>
                <th scope="col" className="px-5 py-3 font-medium">Latest production</th>
                <th scope="col" className="px-5 py-3 font-medium">Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data.map((c) => (
                <tr key={c.id} className="hover:bg-gray-50">
                  <td className="px-5 py-3.5">
                    <Link href={`/campaigns/${c.id}`} className="font-medium text-ink hover:text-red-dark">
                      {c.name}
                    </Link>
                    <p className="text-[13px] text-gray">Owner {c.owner}</p>
                  </td>
                  <td className="px-5 py-3.5 text-ink">{humanise(c.status)}</td>
                  <td className="px-5 py-3.5 text-ink tabular">v{c.current_brief_version}</td>
                  <td className="px-5 py-3.5">
                    {c.latest_job ? (
                      <Link href={`/jobs/${c.latest_job.id}`} className="inline-flex">
                        <StatusBadge kind="job" value={c.latest_job.status} size="sm" />
                        <span className="visually-hidden">, open production</span>
                      </Link>
                    ) : (
                      <span className="text-gray">None</span>
                    )}
                  </td>
                  <td className="px-5 py-3.5 text-gray tabular">{formatDateTime(c.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </>
  );
}
