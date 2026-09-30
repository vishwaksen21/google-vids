"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, X } from "lucide-react";
import { useState } from "react";
import { useBrowserStatus, useSystem } from "@/lib/hooks";

const NAV = [
  { href: "/", label: "Home", match: (p: string) => p === "/" },
  { href: "/campaigns", label: "Campaigns", match: (p: string) => p.startsWith("/campaigns") || p.startsWith("/jobs") },
  { href: "/browser", label: "Google account", match: (p: string) => p.startsWith("/browser") },
  {
    href: "/advanced",
    label: "Advanced",
    match: (p: string) => p.startsWith("/advanced") || p.startsWith("/logs") || p.startsWith("/settings"),
  },
];

/** A one-glance answer to "can this make real videos right now?". */
function ConnectionPill() {
  const { data: sys } = useSystem();
  const { data: browser } = useBrowserStatus();
  let tone = "bg-gray-300";
  let text = "Checking…";
  if (sys?.settings.google_vids_mode === "mock") {
    text = "Practice mode";
  } else if (browser) {
    const state = browser.last_check?.state;
    if (state === "READY" || state === "SIGNED_IN") {
      tone = "bg-red";
      text = browser.last_check?.account ? `Google Vids: ${browser.last_check.account}` : "Google Vids connected";
    } else {
      text = "Google Vids not connected";
    }
  }
  return (
    <Link
      href="/browser"
      className="ml-auto hidden max-w-[280px] items-center gap-2 rounded-full border border-gray-200 px-3 py-1.5 text-[13px] text-ink hover:border-gray-400 md:ml-3 md:flex"
    >
      <span aria-hidden className={`size-2 shrink-0 rounded-full ${tone}`} />
      <span className="truncate">{text}</span>
    </Link>
  );
}

export function SiteHeader() {
  const pathname = usePathname() || "/";
  const [open, setOpen] = useState(false);

  const links = NAV.map((item) => {
    const active = item.match(pathname);
    return (
      <li key={item.href}>
        <Link
          href={item.href}
          aria-current={active ? "page" : undefined}
          onClick={() => setOpen(false)}
          className={`relative flex h-10 items-center rounded-md px-3 text-[14px] font-medium transition-colors duration-150 md:h-16 md:rounded-none md:px-3 ${
            active ? "text-red-dark" : "text-gray hover:text-ink"
          }`}
        >
          {item.label}
          {active ? <span aria-hidden className="absolute inset-x-3 bottom-0 hidden h-[3px] rounded-t bg-red md:block" /> : null}
        </Link>
      </li>
    );
  });

  return (
    <header className="border-b border-gray-200 bg-white">
      <div className="mx-auto flex h-16 max-w-[1320px] items-center gap-4 px-4 sm:px-6 lg:px-8">
        <Link href="/" className="flex shrink-0 items-center gap-3 rounded-md" aria-label="Video Studio home">
          <Image src="/logo.png" alt="Prudential" width={470} height={213} priority className="h-9 w-auto" />
          <span aria-hidden className="hidden h-6 w-px bg-gray-200 sm:block" />
          <span className="hidden text-[15px] font-semibold tracking-[-0.005em] text-ink sm:block">Video Studio</span>
        </Link>

        <nav aria-label="Main" className="ml-auto hidden md:block">
          <ul className="flex items-center gap-1">{links}</ul>
        </nav>

        <ConnectionPill />

        <button
          type="button"
          className="ml-auto rounded-md p-2 text-ink hover:bg-gray-50 md:hidden"
          aria-expanded={open}
          aria-controls="mobile-nav"
          onClick={() => setOpen((v) => !v)}
        >
          {open ? <X aria-hidden className="size-5" /> : <Menu aria-hidden className="size-5" />}
          <span className="visually-hidden">{open ? "Close menu" : "Open menu"}</span>
        </button>
      </div>
      {open ? (
        <nav id="mobile-nav" aria-label="Main" className="border-t border-gray-100 px-4 pb-3 md:hidden">
          <ul className="flex flex-col py-2">{links}</ul>
        </nav>
      ) : null}
    </header>
  );
}
