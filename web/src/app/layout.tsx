import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Unmask",
  description: "Sybil-resistant trust scores for ERC-8004 AI agents, published onchain on Monad.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col">
        <header className="border-b border-[var(--line)]">
          <nav className="mx-auto flex max-w-5xl items-center gap-6 px-4 py-4 text-sm">
            <Link href="/" className="text-base font-semibold tracking-tight">Unmask</Link>
            <Link href="/findings" className="text-[var(--muted)] hover:text-[var(--fg)]">Mainnet findings</Link>
            <Link href="/#integrate" className="text-[var(--muted)] hover:text-[var(--fg)]">Integrate</Link>
            <span className="ml-auto rounded-full border border-[var(--line)] px-2 py-0.5 text-xs text-[var(--muted)]">Monad testnet</span>
          </nav>
        </header>
        <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-10">{children}</main>
        <footer className="border-t border-[var(--line)] py-6 text-center text-xs text-[var(--muted)]">
          Built for Monad Metropolis · Track 04 · data from Envio HyperSync
        </footer>
      </body>
    </html>
  );
}
