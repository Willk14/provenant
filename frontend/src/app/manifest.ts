import type { MetadataRoute } from "next";

// Lets Edge and Chrome install Provenant as a desktop app (its own window, taskbar icon and Start menu entry).
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Provenant",
    short_name: "Provenant",
    description: "Kenya tax, IFRS 16 leases and debt covenants, with every figure traced to its source.",
    start_url: "/home",
    scope: "/",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#2f7d6d",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
