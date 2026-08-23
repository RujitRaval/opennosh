export const brandSurfaces = [
  "rice-paper",
  "commons-ink",
  "signal-tomato",
  "field-acid",
  "one-light",
  "one-dark",
] as const;

export type BrandSurface = (typeof brandSurfaces)[number];

export const brandWordmarks: Record<BrandSurface, { src: string; width: number; height: number }> = {
  "rice-paper": { src: "/brand/wordmark-rice-paper.svg", width: 548, height: 112 },
  "commons-ink": { src: "/brand/wordmark-commons-ink.svg", width: 548, height: 112 },
  "signal-tomato": { src: "/brand/wordmark-signal-tomato.svg", width: 548, height: 112 },
  "field-acid": { src: "/brand/wordmark-field-acid.svg", width: 548, height: 112 },
  "one-light": { src: "/brand/wordmark-one-light.svg", width: 548, height: 112 },
  "one-dark": { src: "/brand/wordmark-one-dark.svg", width: 548, height: 112 },
};
