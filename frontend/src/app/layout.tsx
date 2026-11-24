import type { Metadata } from "next";

import { AppChrome } from "@/components/AppChrome";

import "./globals.css";

export const metadata: Metadata = {
  title: "Provenant",
  description: "Kenya tax, IFRS 16 leases and debt covenants, with every figure traced to its source.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AppChrome>{children}</AppChrome>
      </body>
    </html>
  );
}
