"use client";

import { Lightbulb } from "lucide-react";
import { artifactContentUrl } from "@/lib/api";
import { CATEGORY_LABELS } from "@/lib/status";
import type { Validation } from "@/lib/types";
import { StatusBadge } from "./ui/status-badge";

const SEVERITY_ORDER: Record<string, number> = { BLOCKER: 0, MAJOR: 1, MINOR: 2, INFO: 3 };
const OUTCOME_ORDER: Record<string, number> = { FAIL: 0, NOT_RUN: 1, PASS: 2 };

export function sortValidations(list: Validation[]): Validation[] {
  return [...list].sort(
    (a, b) =>
      (OUTCOME_ORDER[a.outcome] ?? 3) - (OUTCOME_ORDER[b.outcome] ?? 3) ||
      (SEVERITY_ORDER[a.severity] ?? 4) - (SEVERITY_ORDER[b.severity] ?? 4),
  );
}

export function groupByCategory(list: Validation[]): [string, Validation[]][] {
  const map = new Map<string, Validation[]>();
  for (const v of list) {
    const arr = map.get(v.category) ?? [];
    arr.push(v);
    map.set(v.category, arr);
  }
  const order = ["OPERATIONAL", "CONTENT", "BRAND_POLICY", "TECHNICAL"];
  const rank = (c: string) => (order.includes(c) ? order.indexOf(c) : order.length);
  return [...map.entries()].sort((a, b) => rank(a[0]) - rank(b[0]));
}

export function ValidationSummary({ list }: { list: Validation[] }) {
  const failed = list.filter((v) => v.outcome === "FAIL").length;
  const notRun = list.filter((v) => v.outcome === "NOT_RUN").length;
  const passed = list.length - failed - notRun;
  return (
    <p className="text-[13px] text-gray tabular">
      {passed} passed, {failed} failed{notRun ? `, ${notRun} not run` : ""}
    </p>
  );
}

/** Validation results as a list: outcome, severity, rule, message, judge, fix hint, evidence. */
export function ValidationList({ list, emptyText = "No validation results were recorded." }: { list: Validation[]; emptyText?: string }) {
  if (!list.length) return <p className="text-[14px] text-gray">{emptyText}</p>;
  return (
    <ul className="divide-y divide-gray-100 rounded-lg border border-gray-200">
      {sortValidations(list).map((v, i) => (
        <li key={v.id ?? `${v.rule_id}-${i}`} className="grid gap-x-4 gap-y-1.5 px-4 py-3 sm:grid-cols-[112px_1fr]">
          <div className="flex flex-wrap items-start gap-1.5 sm:flex-col">
            <StatusBadge kind="outcome" value={v.outcome} size="sm" />
            <StatusBadge kind="severity" value={v.severity} size="sm" />
          </div>
          <div className="min-w-0">
            <p className="text-[14px] text-ink">{v.message}</p>
            <p className="mt-0.5 text-[12.5px] text-gray">
              <code className="text-gray-700">{v.rule_id}</code>
              <span aria-hidden className="mx-1.5 text-gray-300">
                |
              </span>
              {CATEGORY_LABELS[v.category] ?? v.category}
              <span aria-hidden className="mx-1.5 text-gray-300">
                |
              </span>
              judged by {v.judge}
              {v.subject ? (
                <>
                  <span aria-hidden className="mx-1.5 text-gray-300">
                    |
                  </span>
                  {v.subject}
                </>
              ) : null}
            </p>
            {v.fix_hint ? (
              <p className="mt-1.5 flex gap-1.5 text-[13px] text-gray-700">
                <Lightbulb aria-hidden className="mt-0.5 size-3.5 shrink-0 text-red" />
                <span>
                  <span className="font-medium">Fix: </span>
                  {v.fix_hint}
                </span>
              </p>
            ) : null}
            {v.evidence?.length ? (
              <p className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[12.5px]">
                <span className="text-gray">Evidence:</span>
                {v.evidence.map((id, n) => (
                  <a key={id} href={artifactContentUrl(id)} target="_blank" rel="noreferrer" className="font-medium text-red-dark underline-offset-2 hover:underline">
                    item {n + 1}
                    <span className="visually-hidden"> (artifact {id})</span>
                  </a>
                ))}
              </p>
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  );
}

export function GroupedValidations({ list }: { list: Validation[] }) {
  if (!list.length) return <p className="text-[14px] text-gray">No validation results were recorded.</p>;
  return (
    <div className="space-y-5">
      {groupByCategory(list).map(([cat, items]) => (
        <div key={cat}>
          <div className="mb-2 flex items-baseline justify-between gap-3">
            <h4 className="text-[14px] font-semibold text-ink">{CATEGORY_LABELS[cat] ?? cat}</h4>
            <ValidationSummary list={items} />
          </div>
          <ValidationList list={items} />
        </div>
      ))}
    </div>
  );
}
