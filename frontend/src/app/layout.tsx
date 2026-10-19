import type { Metadata } from "next";
import "./globals.css";

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
      <head>
      </head>
      <body>{children}</body>
    </html>
  );
}
