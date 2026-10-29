import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Alexa+ WebPilot — Simulated Multimodal Device",
  description:
    "Simulated Alexa+ multimodal client for WebPilot: a verifiable web task agent served over MCP 2025-11-25 Streamable HTTP with cryptographic approval gates.",
};

export const viewport: Viewport = {
  themeColor: "#070B14",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans">{children}</body>
    </html>
  );
}
