import type { Metadata } from "next";
import "./globals.css";
import { AppProvider } from "@/components/app/AppProvider";
import AppShell from "@/components/app/AppShell";

export const metadata: Metadata = {
  title: "MulteAgent — Control Deck",
  description: "Local AI assistant with IT, Development and Marketing specialists",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head></head>
      <body>
        {/* The provider + shell keep the sidebar and navbar mounted; only `children` (the page) changes. */}
        <AppProvider>
          <AppShell>{children}</AppShell>
        </AppProvider>
      </body>
    </html>
  );
}
