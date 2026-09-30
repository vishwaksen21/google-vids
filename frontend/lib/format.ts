const dateTime = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});
const timeOnly = new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : dateTime.format(d);
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : timeOnly.format(d);
}

export function formatSeconds(s: number | null | undefined, digits = 1): string {
  if (s == null || !Number.isFinite(s)) return "—";
  return `${s.toFixed(digits)} s`;
}

/** 26.08 → "0:26" (timecode for storyboard rulers). */
export function timecode(s: number): string {
  const whole = Math.max(0, Math.round(s));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export function formatBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

export function elapsed(from: string | null | undefined, to?: string | null): string {
  if (!from) return "—";
  const a = new Date(from).getTime();
  const b = to ? new Date(to).getTime() : NaN;
  if (Number.isNaN(a) || Number.isNaN(b)) return "—";
  const secs = Math.max(0, Math.round((b - a) / 1000));
  if (secs < 60) return `${secs} s`;
  const m = Math.floor(secs / 60);
  return `${m} min ${secs % 60} s`;
}

export function percent(fraction: number | null | undefined): string {
  if (fraction == null || !Number.isFinite(fraction)) return "—";
  return `${Math.round(fraction * 100)}%`;
}

export function shortId(id: string | null | undefined): string {
  return id ? id.slice(-8) : "—";
}
