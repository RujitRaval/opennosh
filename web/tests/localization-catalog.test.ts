import { afterEach, describe, expect, it, vi } from "vitest";

import {
  enCatalog,
  formatMessage,
  formatPlural,
  getCatalog,
  pseudoLocalize,
  resolveCatalogValue,
  validateCatalog,
} from "@/lib/i18n/catalog";
import { isSupportedLanguage, resolveInterfaceLanguage } from "@/lib/routes";

afterEach(() => vi.unstubAllEnvs());

describe("typed interface catalog contract", () => {
  it("accepts the complete English source catalog", () => {
    expect(validateCatalog(enCatalog)).toEqual([]);
  });

  it("reports missing, extra, parameter, and plural drift deterministically", () => {
    const missing = structuredClone(enCatalog) as unknown as Record<string, Record<string, unknown>>;
    delete missing.metadata?.homeTitle;
    expect(validateCatalog(missing)).toContain("Missing message: metadata.homeTitle");

    const missingArrayEntry = structuredClone(enCatalog) as unknown as {
      home: { chapters: Array<{ title: string }> };
    };
    missingArrayEntry.home.chapters.splice(1, 1);
    expect(validateCatalog(missingArrayEntry)).toContain("Missing message: home.chapters.2.title");

    const extra = structuredClone(enCatalog) as unknown as Record<string, unknown>;
    extra.unplanned = { message: "No" };
    expect(validateCatalog(extra)).toContain("Unexpected message: unplanned.message");

    const parameter = structuredClone(enCatalog) as unknown as {
      truth: { release: string; verifiedRecords: unknown };
    };
    parameter.truth.release = "release {build}";
    expect(validateCatalog(parameter)).toContain("Parameter mismatch: truth.release");

    parameter.truth.verifiedRecords = "{count} records";
    expect(validateCatalog(parameter)).toContain("Plural shape mismatch: truth.verifiedRecords");
  });

  it("falls back by exact message key without changing the requested catalog", () => {
    const partial = { metadata: { homeTitle: "Localized title" } };
    expect(resolveCatalogValue(partial, "metadata.homeTitle")).toBe("Localized title");
    expect(resolveCatalogValue(partial, "shell.menu")).toBe("Menu");
    expect(resolveCatalogValue(partial, "missing.key")).toBeUndefined();
  });

  it("preserves parameters while expanding pseudo-localized copy", () => {
    const pseudo = pseudoLocalize("Release {version} is ready");
    expect(pseudo).toContain("{version}");
    expect(pseudo).toMatch(/^［/);
    expect(pseudo).not.toContain("Release");
    expect(getCatalog("en-XA").home.heroLine1).toMatch(/^［/);
  });

  it("formats parameters and plural branches without locale-dependent fallback drift", () => {
    expect(formatMessage("Hello {name}", { name: "Sam" })).toBe("Hello Sam");
    expect(formatPlural(enCatalog.truth.acceptedChanges, 1, "en")).toBe("1 accepted change");
    expect(formatPlural(enCatalog.truth.acceptedChanges, 2, "en")).toBe("2 accepted changes");
    expect(formatPlural(getCatalog("en-XA").truth.acceptedChanges, 2, "en-XA")).toContain("2");
  });

  it("never negotiates the test-only pseudo-locale from browser preferences", () => {
    expect(resolveInterfaceLanguage({ acceptLanguage: "en-XA,en;q=0.5" })).toBe("en");
    expect(resolveInterfaceLanguage({ savedLanguage: "en-XA" })).toBe("en");
  });

  it("exposes the pseudo-locale only behind an explicit non-production test flag", () => {
    vi.stubEnv("NEXT_PUBLIC_OPENNOSH_ENABLE_PSEUDO_LOCALE", "1");
    vi.stubEnv("NODE_ENV", "test");
    expect(isSupportedLanguage("en-XA")).toBe(true);

    vi.stubEnv("NODE_ENV", "production");
    expect(isSupportedLanguage("en-XA")).toBe(false);
  });
});
