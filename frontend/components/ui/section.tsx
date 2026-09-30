import { useId, type ReactNode } from "react";

/** A titled region of a page. Panels are bordered, not shadowed. */
export function Section({
  title,
  description,
  actions,
  children,
  className = "",
  bare = false,
  headingLevel = 2,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bare?: boolean;
  headingLevel?: 2 | 3;
}) {
  const id = useId();
  const H = headingLevel === 2 ? "h2" : "h3";
  return (
    <section aria-labelledby={id} className={`${bare ? "" : "rounded-xl border border-gray-200 bg-white"} ${className}`}>
      <div className={`flex flex-wrap items-end justify-between gap-3 ${bare ? "mb-4" : "border-b border-gray-100 px-5 py-4"}`}>
        <div className="min-w-0">
          <H id={id} className={headingLevel === 2 ? "text-[18px] font-semibold text-ink" : "text-[16px] font-semibold text-ink"}>
            {title}
          </H>
          {description ? <p className="mt-0.5 text-[14px] text-gray">{description}</p> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      <div className={bare ? "" : "px-5 py-4"}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, lead, actions, meta }: { title: ReactNode; lead?: ReactNode; actions?: ReactNode; meta?: ReactNode }) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0 max-w-3xl">
        {meta ? <div className="mb-2 flex flex-wrap items-center gap-2 text-[13px] text-gray">{meta}</div> : null}
        <h1 className="text-[28px] leading-tight font-bold tracking-[-0.01em] text-ink sm:text-[32px]">{title}</h1>
        {lead ? <p className="mt-2 text-[16px] leading-relaxed text-gray">{lead}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function KeyValues({ items, columns = 2 }: { items: { label: ReactNode; value: ReactNode }[]; columns?: 1 | 2 | 3 | 4 }) {
  const cols = { 1: "", 2: "sm:grid-cols-2", 3: "sm:grid-cols-2 lg:grid-cols-3", 4: "sm:grid-cols-2 lg:grid-cols-4" }[columns];
  return (
    <dl className={`grid grid-cols-1 gap-x-8 gap-y-4 ${cols}`}>
      {items.map((it, i) => (
        <div key={i} className="min-w-0">
          <dt className="text-[13px] text-gray">{it.label}</dt>
          <dd className="mt-0.5 text-[15px] break-words text-ink">{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}
