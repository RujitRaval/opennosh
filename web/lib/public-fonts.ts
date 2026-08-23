import localFont from "next/font/local";

export const archivo = localFont({
  src: "../assets/fonts/archivo-latin-variable.woff2",
  variable: "--font-archivo",
  weight: "100 900",
  display: "swap",
  preload: true,
  fallback: ["Arial Narrow", "Arial", "sans-serif"],
});

export const sourceSans = localFont({
  src: "../assets/fonts/source-sans-3-latin-variable.woff2",
  variable: "--font-source-sans",
  weight: "400 700",
  display: "swap",
  preload: true,
  fallback: ["Arial", "sans-serif"],
});

export const plexMono = localFont({
  src: [
    { path: "../assets/fonts/ibm-plex-mono-latin-400.woff2", weight: "400" },
    { path: "../assets/fonts/ibm-plex-mono-latin-500.woff2", weight: "500" },
    { path: "../assets/fonts/ibm-plex-mono-latin-600.woff2", weight: "600" },
  ],
  variable: "--font-plex-mono",
  display: "swap",
  preload: false,
  fallback: ["Courier New", "monospace"],
});
