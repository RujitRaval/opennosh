import { describe, expect, it } from "vitest";

import {
  buildPublicNavigation,
  parsePublicFeatureFlags,
  resolvePublicHub,
} from "@/lib/public-navigation";
import {
  isSupportedLanguage,
  localizePublicPath,
  resolveInterfaceLanguage,
  routes,
} from "@/lib/routes";

describe("public navigation contract", () => {
  it("keeps all four hubs stable while release flags only reveal children", () => {
    const baseline = buildPublicNavigation("en");
    expect(baseline.map((hub) => hub.id)).toEqual([
      "explore",
      "contribute",
      "commons",
      "build",
    ]);
    expect(baseline.find((hub) => hub.id === "explore")?.children).toEqual([]);
    expect(baseline.find((hub) => hub.id === "build")?.children.map((child) => child.id)).toEqual([
      "notices",
    ]);

    const enabled = buildPublicNavigation(
      "en",
      parsePublicFeatureFlags("explorer-search,api-reference,api-reference,unknown"),
    );
    expect(enabled.find((hub) => hub.id === "explore")?.children.map((child) => child.id)).toEqual([
      "search",
    ]);
    expect(enabled.find((hub) => hub.id === "build")?.children.map((child) => child.id)).toEqual([
      "api",
      "notices",
    ]);
  });

  it("resolves hub context for hub pages and their deep children", () => {
    expect(resolvePublicHub(routes.publicHub("commons"), "en")).toBe("commons");
    expect(resolvePublicHub(routes.publicNotices(), "en")).toBe("build");
    expect(resolvePublicHub(routes.publicHome(), "en")).toBeUndefined();
  });

  it("keeps interface language and food-data locale independent", () => {
    expect(
      localizePublicPath({
        pathname: "/en/explore",
        search: "?food_locale=fr-FR&query=rice",
        language: "en",
      }),
    ).toBe("/en/explore?food_locale=fr-FR&query=rice");
    expect(isSupportedLanguage("en")).toBe(true);
    expect(isSupportedLanguage("fr")).toBe(false);
  });

  it("uses saved language first and safely falls back for malformed preferences", () => {
    expect(resolveInterfaceLanguage({ savedLanguage: "en", acceptLanguage: "fr-FR" })).toBe("en");
    expect(resolveInterfaceLanguage({ savedLanguage: "zz", acceptLanguage: "en-US;q=0.8" })).toBe(
      "en",
    );
    expect(resolveInterfaceLanguage({ acceptLanguage: "en;q=0, fr;q=1" })).toBe("en");
    expect(resolveInterfaceLanguage({ acceptLanguage: "not-a-language;q=nope" })).toBe("en");
  });
});
