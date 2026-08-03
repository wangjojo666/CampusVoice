import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "声程 CampusVoice",
    short_name: "声程",
    description: "把校园通知和一句话，变成有证据、先确认、可撤销的个人行动。",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "any",
    background_color: "#f7faf9",
    theme_color: "#0e7f6d",
    lang: "zh-CN",
    icons: [
      { src: "/pwa/icon-192", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/pwa/icon-512", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/pwa/maskable-512", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
