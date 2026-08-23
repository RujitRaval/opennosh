import { describe, expect, it } from "vitest";

import { foodSearch } from "@/lib/api/adapters/foods";
import currentSearch from "@/tests/fixtures/contracts/foods/v1-search.json";
import legacySearch from "@/tests/fixtures/contracts/foods/v0-search.json";

describe("food success contract adapter", () => {
  it("maps identity, license, pack version, provenance, and nulls explicitly", () => {
    const result = foodSearch(
      currentSearch as unknown as Parameters<typeof foodSearch>[0],
    );

    expect(result).toEqual({
      items: [
        {
          id: "community:beans",
          source: "community",
          source_id: "beans",
          name: "Black beans",
          name_local: null,
          category: null,
          attribution: {
            source: "community",
            license: "CC0-1.0",
            source_uri: "https://opennosh.org/packs/starter",
            source_license: null,
            contributed_by: null,
            pack_id: "starter-foods",
            pack_version: "2.4.0",
            provenance: "Community reviewed",
          },
        },
      ],
      limit: 12,
      offset: 0,
      has_more: false,
    });
  });

  it("keeps the N-1 food payload readable with explicit nullable defaults", () => {
    const result = foodSearch(
      legacySearch as unknown as Parameters<typeof foodSearch>[0],
    );

    expect(result.items[0]).toMatchObject({
      id: "usda:123",
      name_local: null,
      category: null,
      attribution: {
        source_uri: null,
        source_license: null,
        contributed_by: null,
        pack_id: null,
        pack_version: null,
        provenance: null,
      },
    });
  });
});
