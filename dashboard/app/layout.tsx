import type { Metadata } from "next";
import { Outfit, DM_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { DataProvider } from "@/lib/data-context";
import { HeaderMeta } from "@/components/header-meta";

const outfit = Outfit({
  subsets: ["latin"],
  variable: "--font-outfit",
  weight: ["300", "400", "500", "600", "700", "800", "900"],
});

const dmMono = DM_Mono({
  subsets: ["latin"],
  variable: "--font-dm-mono",
  weight: ["400", "500"],
});

export const metadata: Metadata = {
  title: "Strikeouts Terminal",
  description:
    "MLB starting-pitcher strikeout model — slate board, performance, and model analytics.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${outfit.variable} ${dmMono.variable}`}>
        <div className="noise" />
        <DataProvider>
          <div className="relative z-10 mx-auto max-w-5xl px-4 sm:px-6">
            <header className="flex items-center justify-between gap-3 py-5">
              <Link href="/" className="flex items-center gap-2.5">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-over to-emerald-700 text-base font-black text-white shadow-[0_0_20px_rgba(16,185,129,0.25)]">
                  K
                </span>
                <span className="text-lg font-bold tracking-tight">
                  Strikeouts Terminal
                </span>
              </Link>
              <nav className="flex items-center gap-1 text-sm">
                <Link
                  href="/"
                  className="rounded-md px-3 py-1.5 text-ink-secondary transition-colors hover:bg-surface-2 hover:text-ink"
                >
                  Slate
                </Link>
                <Link
                  href="/performance"
                  className="rounded-md px-3 py-1.5 text-ink-secondary transition-colors hover:bg-surface-2 hover:text-ink"
                >
                  Performance
                </Link>
                <Link
                  href="/model"
                  className="rounded-md px-3 py-1.5 text-ink-secondary transition-colors hover:bg-surface-2 hover:text-ink"
                >
                  Model
                </Link>
              </nav>
            </header>
            <main className="pb-16">{children}</main>
            <footer className="flex items-center justify-between border-t border-line py-5 text-[11px] text-ink-muted">
              <span className="figure">flat 100u basis</span>
              <HeaderMeta />
            </footer>
          </div>
        </DataProvider>
      </body>
    </html>
  );
}
