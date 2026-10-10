import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { TopNav } from "@/components/TopNav";
import { UserProvider } from "@/lib/user";

// Self-hosted (no network at build or on stage): Inter 300/400/600/700, JetBrains Mono 400/500/700 — the deck's faces.
const inter = localFont({
  variable: "--font-inter",
  display: "swap",
  src: [
    { path: "./fonts/inter-latin-300-normal.woff2", weight: "300" },
    { path: "./fonts/inter-latin-400-normal.woff2", weight: "400" },
    { path: "./fonts/inter-latin-600-normal.woff2", weight: "600" },
    { path: "./fonts/inter-latin-700-normal.woff2", weight: "700" },
  ],
});
const mono = localFont({
  variable: "--font-mono",
  display: "swap",
  src: [
    { path: "./fonts/jetbrains-mono-latin-400-normal.woff2", weight: "400" },
    { path: "./fonts/jetbrains-mono-latin-500-normal.woff2", weight: "500" },
    { path: "./fonts/jetbrains-mono-latin-700-normal.woff2", weight: "700" },
  ],
});

export const metadata: Metadata = {
  title: "Cascade",
  description: "Compile recurring LLM traffic into verified artifacts and account for every token.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body>
        <UserProvider>
          <TopNav />
          <main className="mx-auto max-w-[1180px] px-6 pb-20 pt-9">{children}</main>
        </UserProvider>
      </body>
    </html>
  );
}
