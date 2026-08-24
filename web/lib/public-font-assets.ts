export const publicFontAssetVersion = "v1" as const;

type PublicFontAsset = Readonly<{
  family: "Archivo" | "Source Sans 3" | "IBM Plex Mono";
  path: `../assets/fonts/${typeof publicFontAssetVersion}/${string}.woff2`;
  role: "display" | "body" | "data";
  preload: boolean;
  sha256: string;
}>;

export const publicFontAssets = {
  archivo: {
    family: "Archivo",
    path: "../assets/fonts/v1/archivo-latin-variable.woff2",
    role: "display",
    preload: true,
    sha256: "4c98b9d490d1698ec95f2ff17a6c7d0e72691864c0c5d7bc2a2c161b45afe5ad",
  },
  sourceSans: {
    family: "Source Sans 3",
    path: "../assets/fonts/v1/source-sans-3-latin-variable.woff2",
    role: "body",
    preload: true,
    sha256: "ac057a5593cbe3df0d2585da5dd5f33b8efa84aa30550c710fe061b37fc5c54b",
  },
  plexMono400: {
    family: "IBM Plex Mono",
    path: "../assets/fonts/v1/ibm-plex-mono-latin-400.woff2",
    role: "data",
    preload: false,
    sha256: "c36f509c0a8f9f85f29cb44bc8701d8a9e0b14c499e77a884f789ead7093a7ac",
  },
  plexMono500: {
    family: "IBM Plex Mono",
    path: "../assets/fonts/v1/ibm-plex-mono-latin-500.woff2",
    role: "data",
    preload: false,
    sha256: "a76f53ca6612e7b3828eec2311098675b7f9849ae4169a8bcef6302aec02a6c0",
  },
  plexMono600: {
    family: "IBM Plex Mono",
    path: "../assets/fonts/v1/ibm-plex-mono-latin-600.woff2",
    role: "data",
    preload: false,
    sha256: "ad4580d8cb4b5f627c2d18457656732f7f7b070f7837fbc380e08054157e6f6c",
  },
} as const satisfies Record<string, PublicFontAsset>;
