import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { AppShell } from "@/components/shell/AppShell";
import { THEME_SCRIPT } from "@/lib/theme";

export const metadata: Metadata = {
  title: "AR3 Cockpit",
  description: "Set up, run and watch the AR3 robot arm stack",
  icons: { icon: "/brand/favicon.svg" },
  manifest: "/manifest.webmanifest",
};

export const viewport: Viewport = { themeColor: "#0d6efd" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Applies the stored theme before the first paint (no light flash in dark mode). */}
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
