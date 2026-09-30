import type { Metadata } from "next";
import Link from "next/link";
import { Cpu, ScrollText, SlidersHorizontal } from "lucide-react";
import { PageHeader } from "@/components/ui/section";

export const metadata: Metadata = { title: "Advanced" };

const ITEMS = [
  {
    href: "/advanced/automation",
    icon: Cpu,
    title: "Automation details",
    body: "The automation's Chrome window, execution modes, diagnostics, and which Google Vids actions have been verified.",
  },
  {
    href: "/logs",
    icon: ScrollText,
    title: "Logs",
    body: "Every browser action and decision the automation recorded, with screenshots.",
  },
  {
    href: "/settings",
    icon: SlidersHorizontal,
    title: "Settings",
    body: "Your name for the audit trail, the brand profile, and the backend configuration.",
  },
];

export default function Page() {
  return (
    <>
      <PageHeader title="Advanced" lead="Technical pages for checking and troubleshooting the automation. You don't need these to make videos." />
      <ul className="grid gap-4 md:grid-cols-3">
        {ITEMS.map(({ href, icon: Icon, title, body }) => (
          <li key={href}>
            <Link
              href={href}
              className="group flex h-full flex-col rounded-xl border border-gray-200 bg-white p-5 transition-colors hover:border-red"
            >
              <Icon aria-hidden className="size-5 text-gray group-hover:text-red" />
              <span className="mt-3 text-[16px] font-semibold text-ink group-hover:text-red-dark">{title}</span>
              <span className="mt-1 text-[14px] leading-relaxed text-gray">{body}</span>
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
