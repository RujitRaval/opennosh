import { beforeEach, describe, expect, it, vi } from "vitest";

const cookieValues = vi.hoisted(() => new Map<string, string>());

vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => {
    const value = cookieValues.get(name);
    return value === undefined ? undefined : { value };
  } }),
}));

import TrackerLayout from "@/app/(tracker)/tracker/layout";
import { publicReturnPathCookie } from "@/lib/root-topology";
import { interfaceLanguageCookie } from "@/lib/routes";

beforeEach(() => cookieValues.clear());

describe("tracker root document", () => {
  it("emits the saved supported interface language and remembered public route", async () => {
    cookieValues.set(interfaceLanguageCookie, "en");
    cookieValues.set(publicReturnPathCookie, "/en/explore?food_locale=hi-IN");

    const root = await TrackerLayout({ children: <main>Tracker</main> });
    const body = root.props.children;
    const footer = body.props.children[1];

    expect(root.props.lang).toBe("en");
    expect(root.props["data-surface"]).toBe("tracker");
    expect(root.props["data-interface-language"]).toBe("en");
    expect(footer.props).toMatchObject({
      language: "en",
      publicReturnPath: "/en/explore?food_locale=hi-IN",
    });
  });

  it("rejects unsupported language and cross-origin return preferences", async () => {
    cookieValues.set(interfaceLanguageCookie, "zz");
    cookieValues.set(publicReturnPathCookie, "//example.com/en");

    const root = await TrackerLayout({ children: <main>Tracker</main> });
    const footer = root.props.children.props.children[1];

    expect(root.props.lang).toBe("en");
    expect(footer.props.publicReturnPath).toBe("/en");
  });
});
