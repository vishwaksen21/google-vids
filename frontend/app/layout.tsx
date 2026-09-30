import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import { Providers } from "@/components/providers";
import { SiteHeader } from "@/components/shell/site-header";
import { SiteNotices } from "@/components/shell/system-rail";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

export const metadata: Metadata = {
  title: { default: "Video Studio", template: "%s | Video Studio" },
  description: "Make Prudential Health India videos with Google Vids: describe, approve, generate and download.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#ffffff",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en-IN" className={inter.variable}>
      <body className="min-h-dvh">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:text-ink focus:shadow-soft"
        >
          Skip to content
        </a>
        <Providers>
          <SiteHeader />
          <SiteNotices />
          <main id="main" tabIndex={-1} className="mx-auto max-w-[1320px] px-4 pt-8 pb-24 outline-none sm:px-6 lg:px-8">
            {children}
          </main>
        </Providers>
      </body>
    </html>
  );
}
