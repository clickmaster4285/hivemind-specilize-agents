import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";
import { AppProvider } from "@/components/app/AppProvider";
import AppShell from "@/components/app/AppShell";
import { NONCE_HEADER } from "@/lib/csp";
import { THEME_BOOT_SCRIPT } from "@/lib/themeBoot";

export const metadata: Metadata = {
  title: "MulteAgent — Control Deck",
  description: "Local AI assistant with IT, Development and Marketing specialists",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // the page's CSP nonce (src/proxy.ts): without it the browser refuses the inline theme script
  const nonce = (await headers()).get(NONCE_HEADER) ?? undefined;
  return (
    // data-theme is set by the boot script before React hydrates, so the server's markup can't match it
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* set the colour theme before first paint: no flash of the wrong theme (src/lib/theme.ts) */}
        <script nonce={nonce} dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body>
        {/* The provider + shell keep the sidebar and navbar mounted; only `children` (the page) changes. */}
        <AppProvider>
          <AppShell>{children}</AppShell>
        </AppProvider>
      </body>
    </html>
  );
}
