import { describe, expect, it } from "vitest";

import { foodSearch } from "@/lib/api/adapters/foods";
import legacySearch from "@/tests/fixtures/contracts/foods/v1-search.json";
import currentSearch from "@/tests/fixtures/contracts/foods/v2-search.json";

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
      has_more: true,
      next_cursor: "signed-next-page-cursor",
      snapshot_id: "018f5316-4f4e-7d79-b9f6-88c11a68a497",
      snapshot_expires_at: "2026-08-23T14:30:00Z",
    });
  });

  it("keeps the N-1 offset payload readable with null cursor metadata", () => {
    const result = foodSearch(
      legacySearch as unknown as Parameters<typeof foodSearch>[0],
    );

    expect(result).toMatchObject({
      next_cursor: null,
      snapshot_id: null,
      snapshot_expires_at: null,
    });
    expect(result.items[0]).toMatchObject({
      id: "community:beans",
      name_local: null,
      category: null,
      attribution: {
        source_uri: "https://opennosh.org/packs/starter",
        source_license: null,
        contributed_by: null,
        pack_id: "starter-foods",
        pack_version: "2.4.0",
        provenance: "Community reviewed",
      },
    });
  });
});
