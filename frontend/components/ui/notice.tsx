import { CircleAlert, Info, LoaderCircle, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { errorMessage } from "@/lib/api";

type NoticeTone = "error" | "info" | "attention";

const toneClass: Record<NoticeTone, string> = {
  error: "border-red-200 bg-red-50 text-red-deep",
  attention: "border-red-200 bg-white text-ink",
  info: "border-gray-200 bg-gray-50 text-ink",
};

export function Notice({
  tone = "info",
  title,
  children,
  action,
  className = "",
}: {
  tone?: NoticeTone;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  const Icon = tone === "info" ? Info : CircleAlert;
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={`flex gap-3 rounded-lg border px-4 py-3 text-[14px] ${toneClass[tone]} ${className}`}
    >
      <Icon aria-hidden className={`mt-0.5 size-4 shrink-0 ${tone === "info" ? "text-gray" : "text-red"}`} />
      <div className="min-w-0 flex-1">
        {title ? <p className="font-semibold">{title}</p> : null}
        {children ? <div className={title ? "mt-0.5" : ""}>{children}</div> : null}
      </div>
      {action ? <div className="shrink-0 self-center">{action}</div> : null}
    </div>
  );
}

export function ErrorNotice({ error, title, onRetry, className }: { error: unknown; title?: string; onRetry?: () => void; className?: string }) {
  if (!error) return null;
  return (
    <Notice
      tone="error"
      title={title}
      className={className}
      action={
        onRetry ? (
          <button type="button" onClick={onRetry} className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[13px] font-medium text-red-dark hover:bg-red-100">
            <RefreshCw aria-hidden className="size-3.5" /> Try again
          </button>
        ) : null
      }
    >
      {errorMessage(error)}
    </Notice>
  );
}

export function Loading({ label = "Loading", lines = 3 }: { label?: string; lines?: number }) {
  return (
    <div role="status" aria-live="polite" className="space-y-3 py-2">
      <p className="flex items-center gap-2 text-[13px] text-gray">
        <LoaderCircle aria-hidden className="size-4 animate-spin" />
        {label}
      </p>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} aria-hidden className="h-3 rounded bg-gray-100" style={{ width: `${92 - i * 17}%` }} />
      ))}
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-gray-300 px-6 py-10 text-center">
      <p className="text-[16px] font-semibold text-ink">{title}</p>
      {children ? <div className="mx-auto mt-1 max-w-prose text-[14px] text-gray">{children}</div> : null}
      {action ? <div className="mt-5 flex justify-center">{action}</div> : null}
    </div>
  );
}
