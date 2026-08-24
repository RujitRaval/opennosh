export const brandAssetVersion = "v1" as const;

export const brandColorTokens = [
  "commons-ink",
  "rice-paper",
  "signal-tomato",
  "field-acid",
  "dataset-indigo",
] as const;

export type BrandColorToken = (typeof brandColorTokens)[number];

export const brandSurfaces = [
  "rice-paper",
  "commons-ink",
  "signal-tomato",
  "field-acid",
  "one-light",
  "one-dark",
] as const;

export type BrandSurface = (typeof brandSurfaces)[number];

export type BrandWordmark = Readonly<{
  version: typeof brandAssetVersion;
  src: `/brand/${typeof brandAssetVersion}/${string}.svg`;
  width: 548;
  height: 112;
  open: BrandColorToken;
  nosh: BrandColorToken;
  intendedSurfaces: readonly BrandColorToken[];
  minimumContrast: 3;
}>;

export const brandWordmarks = {
  "rice-paper": { version: brandAssetVersion, src: "/brand/v1/wordmark-rice-paper.svg", width: 548, height: 112, open: "commons-ink", nosh: "signal-tomato", intendedSurfaces: ["rice-paper"], minimumContrast: 3 },
  "commons-ink": { version: brandAssetVersion, src: "/brand/v1/wordmark-commons-ink.svg", width: 548, height: 112, open: "rice-paper", nosh: "field-acid", intendedSurfaces: ["commons-ink"], minimumContrast: 3 },
  "signal-tomato": { version: brandAssetVersion, src: "/brand/v1/wordmark-signal-tomato.svg", width: 548, height: 112, open: "commons-ink", nosh: "rice-paper", intendedSurfaces: ["signal-tomato"], minimumContrast: 3 },
  "field-acid": { version: brandAssetVersion, src: "/brand/v1/wordmark-field-acid.svg", width: 548, height: 112, open: "commons-ink", nosh: "dataset-indigo", intendedSurfaces: ["field-acid"], minimumContrast: 3 },
  "one-light": { version: brandAssetVersion, src: "/brand/v1/wordmark-one-light.svg", width: 548, height: 112, open: "rice-paper", nosh: "rice-paper", intendedSurfaces: ["commons-ink", "dataset-indigo", "signal-tomato"], minimumContrast: 3 },
  "one-dark": { version: brandAssetVersion, src: "/brand/v1/wordmark-one-dark.svg", width: 548, height: 112, open: "commons-ink", nosh: "commons-ink", intendedSurfaces: ["rice-paper", "field-acid", "signal-tomato"], minimumContrast: 3 },
} as const satisfies Record<BrandSurface, BrandWordmark>;
