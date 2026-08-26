import { describe, expect, it } from "vitest";

import { canonicalJson } from "./vertical/reference-client";

describe("vertical reference-client canonical JSON", () => {
  it("sorts object keys by Unicode code point across runtimes", () => {
    const value = {
      "😀": "astral",
      é: "accent",
      a: { "😀": 4, a: 3, A: 2, "!": 1 },
      A: "upper",
      "!": "punctuation",
    };

    expect(canonicalJson(value)).toBe(
      "{\"!\":\"punctuation\",\"A\":\"upper\",\"a\":{\"!\":1,\"A\":2,\"a\":3,\"😀\":4},\"é\":\"accent\",\"😀\":\"astral\"}",
    );
  });
});
