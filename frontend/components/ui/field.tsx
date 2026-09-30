"use client";

import { useId, type ComponentProps, type ReactNode } from "react";

const control =
  "block w-full rounded-md border bg-white px-3 text-[15px] text-ink placeholder:text-gray transition-colors duration-150 " +
  "hover:border-gray-400 focus:border-red focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-red " +
  "disabled:bg-gray-50 disabled:text-gray";

function borderFor(error?: string) {
  return error ? "border-red" : "border-gray-300";
}

interface FieldChrome {
  label: ReactNode;
  hint?: ReactNode;
  error?: string;
  optional?: boolean;
  className?: string;
}

function Chrome({ id, label, hint, error, optional, className = "", children }: FieldChrome & { id: string; children: ReactNode }) {
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1.5 block text-[14px] font-medium text-ink">
        {label}
        {optional ? <span className="ml-1.5 font-normal text-gray">(optional)</span> : null}
      </label>
      {children}
      {hint && !error ? (
        <p id={`${id}-hint`} className="mt-1.5 text-[13px] text-gray">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-error`} className="mt-1.5 text-[13px] font-medium text-red-dark">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function describedBy(id: string, hint?: ReactNode, error?: string) {
  return error ? `${id}-error` : hint ? `${id}-hint` : undefined;
}

export function TextField({ label, hint, error, optional, className, id: givenId, ...rest }: FieldChrome & ComponentProps<"input">) {
  const auto = useId();
  const id = givenId ?? auto;
  return (
    <Chrome id={id} label={label} hint={hint} error={error} optional={optional} className={className}>
      <input
        id={id}
        className={`${control} h-10 ${borderFor(error)}`}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, hint, error)}
        {...rest}
      />
    </Chrome>
  );
}

export function TextArea({ label, hint, error, optional, className, id: givenId, rows = 4, ...rest }: FieldChrome & ComponentProps<"textarea">) {
  const auto = useId();
  const id = givenId ?? auto;
  return (
    <Chrome id={id} label={label} hint={hint} error={error} optional={optional} className={className}>
      <textarea
        id={id}
        rows={rows}
        className={`${control} py-2 leading-relaxed ${borderFor(error)}`}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, hint, error)}
        {...rest}
      />
    </Chrome>
  );
}

export function SelectField({ label, hint, error, optional, className, id: givenId, children, ...rest }: FieldChrome & ComponentProps<"select">) {
  const auto = useId();
  const id = givenId ?? auto;
  return (
    <Chrome id={id} label={label} hint={hint} error={error} optional={optional} className={className}>
      <select
        id={id}
        className={`${control} h-10 pr-8 ${borderFor(error)}`}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, hint, error)}
        {...rest}
      >
        {children}
      </select>
    </Chrome>
  );
}

export function Checkbox({ label, hint, className = "", id: givenId, ...rest }: { label: ReactNode; hint?: ReactNode; className?: string } & ComponentProps<"input">) {
  const auto = useId();
  const id = givenId ?? auto;
  return (
    <div className={`flex items-start gap-2.5 ${className}`}>
      <input id={id} type="checkbox" className="mt-0.5 size-4 shrink-0" aria-describedby={hint ? `${id}-hint` : undefined} {...rest} />
      <div>
        <label htmlFor={id} className="text-[14px] text-ink">
          {label}
        </label>
        {hint ? (
          <p id={`${id}-hint`} className="text-[13px] text-gray">
            {hint}
          </p>
        ) : null}
      </div>
    </div>
  );
}
