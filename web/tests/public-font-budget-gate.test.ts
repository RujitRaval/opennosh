import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

import { afterEach, describe, expect, it } from "vitest";

const webRoot = process.cwd();
const manifestPath = path.join(webRoot, "assets/fonts/v2/font-build.v2.json");
const temporaryDirectories: string[] = [];

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    rmSync(directory, { recursive: true, force: true });
  }
});

describe("public font budget release gate", () => {
  it("rejects any nonzero Living Commons font budget for Tracker", () => {
    const temporaryDirectory = mkdtempSync(path.join(tmpdir(), "opennosh-font-budget-"));
    temporaryDirectories.push(temporaryDirectory);

    const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as {
      budgets: { trackerBytes: number };
    };
    manifest.budgets.trackerBytes = 1;
    const invalidManifestPath = path.join(temporaryDirectory, "font-build.v2.json");
    writeFileSync(invalidManifestPath, JSON.stringify(manifest));

    const result = spawnSync(
      process.execPath,
      [
        "--disable-warning=MODULE_TYPELESS_PACKAGE_JSON",
        "scripts/check_public_font_budgets.mjs",
      ],
      {
        cwd: webRoot,
        encoding: "utf8",
        env: {
          ...process.env,
          OPENNOSH_FONT_BUILD_MANIFEST: invalidManifestPath,
        },
      },
    );

    expect(result.status).toBe(1);
    expect(result.stderr).toContain(
      "Tracker Living Commons font budget must remain exactly zero bytes.",
    );
  });
});
