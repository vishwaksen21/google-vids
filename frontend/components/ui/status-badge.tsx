import { Circle, CircleCheck, CircleMinus, CircleX, Hand, TriangleAlert } from "lucide-react";
import { statusMeta, type StatusKind, type Tone } from "@/lib/status";

const toneClass: Record<Tone, string> = {
  done: "bg-gray-50 border-gray-300 text-ink",
  warn: "bg-white border-gray-400 text-ink",
  live: "bg-white border-red text-red-dark",
  attention: "bg-red-50 border-red-200 text-red-deep",
  fail: "bg-red border-red text-white",
  idle: "bg-white border-gray-200 text-gray",
  muted: "bg-gray-50 border-dashed border-gray-300 text-gray",
};

export function ToneIcon({ tone, className = "size-3.5" }: { tone: Tone; className?: string }) {
  switch (tone) {
    case "done":
      return <CircleCheck aria-hidden className={className} strokeWidth={2.2} />;
    case "warn":
      return <TriangleAlert aria-hidden className={className} strokeWidth={2.2} />;
    case "live":
      return (
        <span aria-hidden className="relative inline-flex size-3.5 items-center justify-center">
          <span className="size-2 rounded-full bg-red animate-tally" />
        </span>
      );
    case "attention":
      return <Hand aria-hidden className={className} strokeWidth={2.2} />;
    case "fail":
      return <CircleX aria-hidden className={className} strokeWidth={2.2} />;
    case "muted":
      return <CircleMinus aria-hidden className={className} strokeWidth={2} />;
    default:
      return <Circle aria-hidden className={className} strokeWidth={2} />;
  }
}

export function StatusBadge({
  kind,
  value,
  label,
  size = "md",
  className = "",
}: {
  kind: StatusKind;
  value: string | null | undefined;
  label?: string;
  size?: "sm" | "md";
  className?: string;
}) {
  const meta = statusMeta(kind, value);
  const sz = size === "sm" ? "h-6 px-2 text-[12px]" : "h-7 px-2.5 text-[13px]";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border font-medium whitespace-nowrap ${sz} ${toneClass[meta.tone]} ${className}`}
      data-tone={meta.tone}
      data-status={value ?? ""}
    >
      <ToneIcon tone={meta.tone} />
      {label ?? meta.label}
    </span>
  );
}
