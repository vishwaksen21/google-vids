"use client";

import { FileText } from "lucide-react";
import useSWR from "swr";
import { artifactContentUrl } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import type { Artifact } from "@/lib/types";

/**
 * Renders an artifact through the proxy: <video> for clips/exports, <img> for frames and
 * screenshots, a link otherwise. Metadata (kind, mime) comes from GET /api/artifacts/{id}.
 */
export function ArtifactView({ id, alt, className = "", caption = true }: { id: string; alt?: string; className?: string; caption?: boolean }) {
  const { data, error } = useSWR<Artifact>(`artifacts/${id}`, { revalidateOnFocus: false, dedupingInterval: 60000 });
  const src = artifactContentUrl(id);

  if (error) {
    return <div className={`flex aspect-video items-center justify-center rounded-lg border border-dashed border-gray-300 p-3 text-center text-[12px] text-gray ${className}`}>Artifact unavailable</div>;
  }
  if (!data) {
    return <div aria-hidden className={`aspect-video rounded-lg bg-gray-100 ${className}`} />;
  }
  const label = typeof data.meta?.label === "string" ? data.meta.label : data.kind.toLowerCase();
  const description = alt ?? `${label}${data.simulated ? " (simulated)" : ""}`;
  let media;
  if (data.mime.startsWith("video/")) {
    media = (
      <video controls preload="metadata" className="aspect-video w-full rounded-lg bg-ink" aria-label={description}>
        <source src={src} type={data.mime} />
        Your browser cannot play this video. <a href={src}>Download it</a>.
      </video>
    );
  } else if (data.mime.startsWith("image/")) {
    media = (
      <a href={src} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-lg border border-gray-200 bg-gray-50">
        {/* Served by our authenticated proxy; next/image optimisation would need server-side access to it. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={src} alt={description} loading="lazy" className="aspect-video w-full object-cover" />
      </a>
    );
  } else {
    media = (
      <a href={src} target="_blank" rel="noreferrer" className="flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-[13px] text-ink hover:border-red">
        <FileText aria-hidden className="size-4 text-gray" /> {data.kind} ({data.mime})
      </a>
    );
  }
  return (
    <figure className={className}>
      {media}
      {caption ? (
        <figcaption className="mt-1.5 flex flex-wrap justify-between gap-x-2 text-[12px] text-gray">
          <span>{label}</span>
          <span className="tabular">
            {formatBytes(data.size_bytes)}
            {data.simulated ? " | simulated" : ""}
          </span>
        </figcaption>
      ) : null}
    </figure>
  );
}
