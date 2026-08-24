export const publicFontAssetVersion = "v2" as const;

type PublicFontAsset = Readonly<{
  family: "opennosh Display" | "opennosh Sans" | "opennosh Mono";
  href: `/fonts/${typeof publicFontAssetVersion}/${string}.woff2`;
  role: "display" | "body" | "data";
  script: "latin";
  delivery: "critical" | "deferred";
  bytes: number;
  sha256: string;
}>;

export const publicFontAssets = {
  archivo: {
    family: "opennosh Display",
    href: "/fonts/v2/opennosh-display-latin-variable.woff2",
    role: "display",
    script: "latin",
    delivery: "critical",
    bytes: 61676,
    sha256: "1adb41b88aaf62be29e2e835b356ade1385ff59d7e60f71f1a20ee4bdc4679fe",
  },
  sourceSans: {
    family: "opennosh Sans",
    href: "/fonts/v2/opennosh-sans-latin-variable.woff2",
    role: "body",
    script: "latin",
    delivery: "critical",
    bytes: 28016,
    sha256: "9c2e5b4f05d07448244df778a775589557d971b0d39f32cfbc263c5e445439e0",
  },
  plexMono400: {
    family: "opennosh Mono",
    href: "/fonts/v2/opennosh-mono-latin-400.woff2",
    role: "data",
    script: "latin",
    delivery: "deferred",
    bytes: 9384,
    sha256: "d5efccc413ca81bfcb2a94a781e1cad6e5fc2616d5e26bfed9420ce779bbbe3b",
  },
  plexMono500: {
    family: "opennosh Mono",
    href: "/fonts/v2/opennosh-mono-latin-500.woff2",
    role: "data",
    script: "latin",
    delivery: "deferred",
    bytes: 9372,
    sha256: "60540ab9e42002d08971f4daf1b413c3b1db19764226875df1bb774fdcab9951",
  },
  plexMono600: {
    family: "opennosh Mono",
    href: "/fonts/v2/opennosh-mono-latin-600.woff2",
    role: "data",
    script: "latin",
    delivery: "deferred",
    bytes: 9412,
    sha256: "c86dfce44d15b2edff518a712e05ef46d2e576d83fd2e0bc28d28d4e4a05ede1",
  },
} as const satisfies Record<string, PublicFontAsset>;

export const publicFontScripts = {
  en: "latin",
  "en-XA": "latin",
} as const;
