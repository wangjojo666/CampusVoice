import type { Metadata, Viewport } from "next";

import { AppShell } from "@/components/layout/app-shell";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "声程 CampusVoice",
    template: "%s · 声程",
  },
  description: "把校园通知和一句话，变成有证据、先确认、可撤销的个人行动。",
  applicationName: "声程 CampusVoice",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, statusBarStyle: "default", title: "声程" },
  icons: {
    icon: [
      { url: "/pwa/icon-192", type: "image/png", sizes: "192x192" },
      { url: "/pwa/icon-512", type: "image/png", sizes: "512x512" },
    ],
    apple: [{ url: "/pwa/icon-192", type: "image/png", sizes: "192x192" }],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#0e7f6d",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" data-scroll-behavior="smooth">
      <head>
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-status-bar-style" content="default" />
        <meta name="apple-mobile-web-app-title" content="声程" />
      </head>
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
