import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { TopNav } from "@/components/TopNav";
import { UserProvider } from "@/lib/user";

const inter = Inter({ subsets: ["latin"], weight: ["300", "400", "600", "700"], variable: "--font-inter" });
const mono = JetBrains_Mono({ subsets: ["latin"], weight: ["400", "500", "700"], variable: "--font-mono" });

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
