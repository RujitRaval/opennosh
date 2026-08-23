import { describe, expect, it } from "vitest";

import { resolvePublicMotionPolicy } from "@/lib/public-motion-policy";

const capableDevice = {
  decorationsEnabled: true,
  reducedMotion: false,
  saveData: false,
  effectiveType: "4g",
  hardwareConcurrency: 8,
  deviceMemory: 8,
};

describe("public motion policy", () => {
  it("loads the optional runtime only for an eligible device", () => {
    expect(resolvePublicMotionPolicy(capableDevice)).toEqual({
      mode: "eligible",
      reason: "eligible",
      loadOptionalRuntime: true,
    });
  });

  it.each([
    [{ ...capableDevice, decorationsEnabled: false }, "kill-switch"],
    [{ ...capableDevice, reducedMotion: true }, "reduced-motion"],
    [{ ...capableDevice, saveData: true }, "data-saver"],
  ] as const)("keeps decoration off for an explicit preference gate", (environment, reason) => {
    expect(resolvePublicMotionPolicy(environment)).toEqual({
      mode: "off",
      reason,
      loadOptionalRuntime: false,
    });
  });

  it.each([
    { ...capableDevice, effectiveType: "2g" },
    { ...capableDevice, hardwareConcurrency: 2 },
    { ...capableDevice, deviceMemory: 2 },
  ])("uses the static low-power presentation without loading decoration", (environment) => {
    expect(resolvePublicMotionPolicy(environment)).toEqual({
      mode: "limited",
      reason: "low-power",
      loadOptionalRuntime: false,
    });
  });
});
