import { describe, expect, it } from "vitest";

import { routeCssFiles } from "../scripts/production_font_isolation_helpers.mjs";

describe("production font isolation manifest parsing", () => {
  it("finds and deduplicates CSS for a dynamic Tracker root", () => {
    const source = `globalThis.__RSC_MANIFEST["/(tracker)/tracker/page"] = ${JSON.stringify({
      entryCSSFiles: {
        "[project]/app/(tracker)/tracker/layout": [
          { path: "static/chunks/base.css", inlined: false },
          { path: "static/chunks/tracker.css", inlined: false },
        ],
        "[project]/app/(tracker)/tracker/page": [
          { path: "static/chunks/tracker.css", inlined: false },
        ],
        "[project]/app/(public)/[language]/layout": [
          { path: "static/chunks/public.css", inlined: false },
        ],
      },
    })};`;

    expect(routeCssFiles(source, "[project]/app/(tracker)/tracker/"))
      .toEqual(["static/chunks/base.css", "static/chunks/tracker.css"]);
  });

  it("fails closed when the dynamic route has no scoped CSS entry", () => {
    expect(() => routeCssFiles(
      'globalThis.__RSC_MANIFEST["route"] = {"entryCSSFiles":{}};',
      "[project]/app/(tracker)/tracker/",
    )).toThrow(/no CSS entries/);
  });
});
