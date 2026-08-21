import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FoodAttributionLine } from "@/components/foods/food-attribution";
import { AddFoodDialog } from "@/components/log/add-food-dialog";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderDialog(
  onAdded = vi.fn(async () => undefined),
  onExpired = vi.fn(),
  onClose = vi.fn(),
) {
  render(
    <AddFoodDialog
      day="2026-08-20"
      onClose={onClose}
      onAdded={onAdded}
      onExpired={onExpired}
    />,
  );
  return { onAdded, onExpired, onClose };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.cookie = "opennosh_csrf=; Max-Age=0; Path=/";
});

describe("food search, barcode, and custom entry", () => {
  it("hides barcode lookup when the integration is disabled", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        expect(String(input)).toBe("/api/v1/foods/capabilities");
        return json({ barcode_lookup_enabled: false });
      }),
    );

    renderDialog();

    expect(screen.queryByRole("tab", { name: /barcode/i })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /custom food/i })).toBeVisible();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  });

  it("supports arrow-key navigation across the available entry tabs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ barcode_lookup_enabled: true })),
    );
    renderDialog();

    const searchTab = screen.getByRole("tab", { name: /search/i });
    const barcodeTab = await screen.findByRole("tab", { name: /barcode/i });
    searchTab.focus();
    fireEvent.keyDown(searchTab, { key: "ArrowRight" });
    await waitFor(() => expect(barcodeTab).toHaveFocus());
    expect(barcodeTab).toHaveAttribute("aria-controls", "food-entry-panel-barcode");
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Barcode");

    fireEvent.keyDown(barcodeTab, { key: "End" });
    const customTab = screen.getByRole("tab", { name: /custom food/i });
    await waitFor(() => expect(customTab).toHaveFocus());
    fireEvent.keyDown(customTab, { key: "Home" });
    await waitFor(() => expect(searchTab).toHaveFocus());
    fireEvent.keyDown(searchTab, { key: "ArrowLeft" });
    await waitFor(() => expect(customTab).toHaveFocus());
  });

  it("keeps barcode lookup hidden when the capability request fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new Error("offline"))));

    renderDialog();

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("tab", { name: /barcode/i })).not.toBeInTheDocument();
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Search");
  });

  it("debounces source-aware search and preserves ranked server order", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/foods/capabilities") {
        return json({ barcode_lookup_enabled: false });
      }
      if (url.startsWith("/api/v1/foods/search?")) {
        const search = new URL(url, "http://localhost").searchParams;
        expect(search.get("source")).toBe("usda");
        return json({
          items: [
            {
              id: "usda:1",
              source: "usda",
              source_id: "1",
              name: "Tofu, firm",
              name_local: null,
              category: "Legumes",
              attribution: { license: "CC0-1.0", contributed_by: null },
            },
            {
              id: "usda:2",
              source: "usda",
              source_id: "2",
              name: "Tofu dessert",
              name_local: null,
              category: "Desserts",
              attribution: { license: "CC0-1.0", contributed_by: null },
            },
          ],
          limit: 12,
          offset: 0,
          has_more: false,
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDialog();

    fireEvent.click(screen.getByRole("radio", { name: /usda/i }));
    fireEvent.change(screen.getByLabelText(/search the food catalogue/i), {
      target: { value: "tofu" },
    });

    const results = await screen.findAllByRole("radio", { name: /tofu/i });
    expect(results.map((result) => result.getAttribute("value"))).toEqual(["usda:1", "usda:2"]);
    expect(fetchMock.mock.calls.filter(([input]) => String(input).startsWith("/api/v1/foods/search?")).length).toBe(1);
    expect(screen.getAllByText(/USDA · CC0-1.0/i)).toHaveLength(2);
  });

  it("rejects a short forced search and ignores an older search response", async () => {
    const olderSearch = deferred<Response>();
    const newerSearch = deferred<Response>();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/foods/capabilities") {
          return json({ barcode_lookup_enabled: false });
        }
        if (url.includes("q=tofu")) return olderSearch.promise;
        if (url.includes("q=lentil")) return newerSearch.promise;
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    renderDialog();

    fireEvent.change(screen.getByLabelText(/search the food catalogue/i), {
      target: { value: "x" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^search$/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(/at least two/i);

    fireEvent.change(screen.getByLabelText(/search the food catalogue/i), {
      target: { value: "tofu" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^search$/i }));
    fireEvent.change(screen.getByLabelText(/search the food catalogue/i), {
      target: { value: "lentil" },
    });
    await waitFor(() =>
      expect(
        vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("q=lentil")),
      ).toBe(true),
    );
    newerSearch.resolve(
      json({
        items: [
          {
            id: "community:lentil-stew",
            source: "community",
            source_id: "lentil-stew",
            name: "Lentil stew",
            name_local: null,
            category: "Meals",
            attribution: { license: "CC-BY-4.0" },
          },
        ],
        limit: 12,
        offset: 0,
        has_more: false,
      }),
    );
    expect(await screen.findByRole("radio", { name: /lentil stew/i })).toBeVisible();
    olderSearch.resolve(
      json({
        items: [
          {
            id: "usda:tofu",
            source: "usda",
            source_id: "tofu",
            name: "Old tofu result",
            name_local: null,
            category: null,
            attribution: { license: "CC0-1.0" },
          },
        ],
        limit: 12,
        offset: 0,
        has_more: false,
      }),
    );
    await waitFor(() => expect(screen.queryByText("Old tofu result")).not.toBeInTheDocument());
    expect(screen.getByText("Lentil stew")).toBeVisible();
  });

  it("loads named portions for a selected result and expires a rejected search session", async () => {
    const onExpired = vi.fn();
    let searchStatus = 200;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/foods/capabilities") {
          return json({ barcode_lookup_enabled: false });
        }
        if (url.startsWith("/api/v1/foods/search?")) {
          if (searchStatus === 401) return json({ detail: "Session expired" }, 401);
          return json({
            items: [
              {
                id: "usda:1",
                source: "usda",
                source_id: "1",
                name: "Tofu",
                name_local: null,
                category: "Legumes",
                attribution: { license: "CC0-1.0" },
              },
            ],
            limit: 12,
            offset: 0,
            has_more: false,
          });
        }
        if (url === "/api/v1/foods/usda/1") {
          return json({
            id: "usda:1",
            source: "usda",
            source_id: "1",
            name: "Tofu",
            name_local: null,
            category: "Legumes",
            attribution: { license: "CC0-1.0" },
            nutrients: {},
            portions: [{ name: "slice", grams: "25" }],
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    renderDialog(undefined, onExpired);

    const input = screen.getByLabelText(/search the food catalogue/i);
    fireEvent.change(input, { target: { value: "tofu" } });
    fireEvent.click(screen.getByRole("button", { name: /^search$/i }));
    fireEvent.click(await screen.findByRole("radio", { name: /^tofu/i }));
    expect(await screen.findByRole("option", { name: /slice \(25 g\)/i })).toBeInTheDocument();

    searchStatus = 401;
    fireEvent.change(input, { target: { value: "tempeh" } });
    fireEvent.click(screen.getByRole("button", { name: /^search$/i }));
    await waitFor(() => expect(onExpired).toHaveBeenCalledTimes(1));
  });

  it("shows barcode lookup only when enabled and recovers after a failed lookup", async () => {
    let lookups = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/foods/capabilities") {
          return json({ barcode_lookup_enabled: true });
        }
        if (url.startsWith("/api/v1/foods/barcode/")) {
          lookups += 1;
          if (lookups === 1) return json({ detail: "Barcode not found in Open Food Facts." }, 404);
          return json({
            id: "openfoodfacts:3017620422003",
            source: "openfoodfacts",
            source_id: "3017620422003",
            barcode: "3017620422003",
            name: "Hazelnut spread",
            brand: "Example",
            nutrients: {},
            portions: [{ name: "tablespoon", grams: "15" }],
            attribution: {
              source: "openfoodfacts",
              source_url: "https://world.openfoodfacts.org/product/3017620422003",
              database_license: "ODbL-1.0",
              contents_license: "DbCL-1.0",
              attribution_text: "Open Food Facts contributors",
            },
            cached: false,
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    renderDialog();

    const barcodeTab = await screen.findByRole("tab", { name: /barcode/i });
    fireEvent.click(barcodeTab);
    const input = screen.getByLabelText(/scan or enter a barcode/i);
    fireEvent.change(input, { target: { value: "3017620422003" } });
    fireEvent.click(screen.getByRole("button", { name: /look up barcode/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not found/i);

    fireEvent.click(screen.getByRole("button", { name: /look up barcode/i }));
    expect(await screen.findByRole("heading", { name: /log hazelnut spread/i })).toBeVisible();
    expect(screen.getByText(/ODbL 1.0 \/ DbCL 1.0/i)).toBeVisible();
    expect(screen.getByRole("option", { name: /tablespoon \(15 g\)/i })).toBeInTheDocument();
  });

  it("validates barcode length and prevents duplicate in-flight lookups", async () => {
    const pendingLookup = deferred<Response>();
    let lookups = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/foods/capabilities") {
          return json({ barcode_lookup_enabled: true });
        }
        if (url.startsWith("/api/v1/foods/barcode/")) {
          lookups += 1;
          return pendingLookup.promise;
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    renderDialog();

    fireEvent.click(await screen.findByRole("tab", { name: /barcode/i }));
    const barcode = screen.getByLabelText(/scan or enter a barcode/i);
    fireEvent.change(barcode, { target: { value: "123" } });
    const form = screen.getByRole("button", { name: /look up barcode/i }).closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);
    expect(screen.getByRole("alert")).toHaveTextContent(/8, 12, 13, or 14 digit/i);
    expect(lookups).toBe(0);

    fireEvent.change(barcode, { target: { value: "3017-6204 22003" } });
    fireEvent.submit(form as HTMLFormElement);
    fireEvent.submit(form as HTMLFormElement);
    expect(lookups).toBe(1);
    pendingLookup.resolve(
      json({
        id: "openfoodfacts:3017620422003",
        source: "openfoodfacts",
        source_id: "3017620422003",
        barcode: "3017620422003",
        name: "Hazelnut spread",
        brand: null,
        nutrients: {},
        portions: [],
        attribution: {
          source_url: "https://world.openfoodfacts.org/product/3017620422003",
          attribution_text: "Open Food Facts contributors",
        },
        cached: false,
      }),
    );
    expect(await screen.findByRole("heading", { name: /log hazelnut spread/i })).toBeVisible();
  });

  it("ignores a barcode response after the user changes entry method", async () => {
    const pendingLookup = deferred<Response>();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/foods/capabilities") {
          return json({ barcode_lookup_enabled: true });
        }
        if (url.startsWith("/api/v1/foods/barcode/")) return pendingLookup.promise;
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    renderDialog();

    fireEvent.click(await screen.findByRole("tab", { name: /barcode/i }));
    fireEvent.change(screen.getByLabelText(/scan or enter a barcode/i), {
      target: { value: "3017620422003" },
    });
    fireEvent.click(screen.getByRole("button", { name: /look up barcode/i }));
    fireEvent.click(screen.getByRole("tab", { name: /custom food/i }));
    pendingLookup.resolve(
      json({
        id: "openfoodfacts:3017620422003",
        source: "openfoodfacts",
        source_id: "3017620422003",
        barcode: "3017620422003",
        name: "Late food",
        brand: null,
        nutrients: {},
        portions: [],
        attribution: {
          source_url: "https://world.openfoodfacts.org/product/3017620422003",
          attribution_text: "Open Food Facts contributors",
        },
        cached: false,
      }),
    );

    expect(await screen.findByText(/only your account can use this entry/i)).toBeVisible();
    await waitFor(() => expect(screen.queryByRole("heading", { name: /log late food/i })).not.toBeInTheDocument());
  });

  it("creates a private food and logs a named portion with CSRF protection", async () => {
    document.cookie = "opennosh_csrf=custom-csrf; Path=/";
    const onAdded = vi.fn(async () => undefined);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/foods/capabilities") {
        return json({ barcode_lookup_enabled: false });
      }
      if (url === "/api/v1/foods/custom") {
        expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("custom-csrf");
        return json({
          id: "e650490a-068a-444b-83ff-c4d1cc18158e",
          source: "custom",
          source_id: "e650490a-068a-444b-83ff-c4d1cc18158e",
          name: "My lentil stew",
          nutrients: {},
          portions: [{ name: "bowl", grams: "325" }],
          private: true,
        }, 201);
      }
      if (url === "/api/v1/logs") {
        const body = JSON.parse(String(init?.body));
        expect(body.quantity).toEqual({ amount: "1", unit: "portion", portion_name: "bowl" });
        return json({ id: "log-id" }, 201);
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDialog(onAdded);

    fireEvent.click(screen.getByRole("tab", { name: /custom food/i }));
    expect(screen.getByText(/only your account can use this entry/i)).toBeVisible();
    fireEvent.change(screen.getByLabelText(/food name/i), { target: { value: "My lentil stew" } });
    fireEvent.change(screen.getByLabelText(/^calories$/i), { target: { value: "165" } });
    fireEvent.change(screen.getByLabelText(/protein/i), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText(/carbohydrate/i), { target: { value: "20" } });
    fireEvent.change(screen.getByLabelText(/^fat/i), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText(/portion name/i), { target: { value: "bowl" } });
    fireEvent.change(screen.getByLabelText(/weight/i), { target: { value: "325" } });
    fireEvent.click(screen.getByRole("button", { name: /save private food/i }));

    expect(await screen.findByRole("heading", { name: /log my lentil stew/i })).toBeVisible();
    expect(screen.getByText(/private to your account/i)).toBeVisible();
    expect(screen.getByLabelText(/measure/i)).toHaveValue("bowl");
    fireEvent.click(screen.getByRole("button", { name: /add my lentil stew/i }));
    await waitFor(() => expect(onAdded).toHaveBeenCalledWith("My lentil stew"));
  });

  it("validates custom nutrition and paired portion fields before saving", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/foods/capabilities") {
        return json({ barcode_lookup_enabled: false });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDialog();

    fireEvent.click(screen.getByRole("tab", { name: /custom food/i }));
    fireEvent.change(screen.getByLabelText(/food name/i), { target: { value: "My tofu" } });
    fireEvent.change(screen.getByLabelText(/^calories$/i), { target: { value: "500" } });
    fireEvent.change(screen.getByLabelText(/protein/i), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText(/carbohydrate/i), { target: { value: "20" } });
    fireEvent.change(screen.getByLabelText(/^fat/i), { target: { value: "5" } });
    const form = screen.getByRole("button", { name: /save private food/i }).closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);
    expect(screen.getByRole("alert")).toHaveTextContent(/within 15%/i);

    fireEvent.change(screen.getByLabelText(/^calories$/i), { target: { value: "165" } });
    fireEvent.change(screen.getByLabelText(/portion name/i), { target: { value: "bowl" } });
    fireEvent.submit(form as HTMLFormElement);
    expect(screen.getByRole("alert")).toHaveTextContent(/both a portion name/i);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("recovers from a custom-food save error and expires a rejected session", async () => {
    const onExpired = vi.fn();
    let attempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/foods/capabilities") {
          return json({ barcode_lookup_enabled: false });
        }
        if (url === "/api/v1/foods/custom") {
          attempts += 1;
          return attempts === 1
            ? json({ detail: "Custom food is temporarily unavailable" }, 503)
            : json({ detail: "Session expired" }, 401);
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    renderDialog(undefined, onExpired);

    fireEvent.click(screen.getByRole("tab", { name: /custom food/i }));
    fireEvent.change(screen.getByLabelText(/food name/i), { target: { value: "My tofu" } });
    fireEvent.change(screen.getByLabelText(/^calories$/i), { target: { value: "165" } });
    fireEvent.change(screen.getByLabelText(/protein/i), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText(/carbohydrate/i), { target: { value: "20" } });
    fireEvent.change(screen.getByLabelText(/^fat/i), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: /save private food/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/temporarily unavailable/i);
    fireEvent.click(screen.getByRole("button", { name: /save private food/i }));
    await waitFor(() => expect(onExpired).toHaveBeenCalledTimes(1));
    expect(attempts).toBe(2);
  });

  it("prevents duplicate custom-food submissions", async () => {
    document.cookie = "opennosh_csrf=custom-csrf; Path=/";
    const pendingCreate = deferred<Response>();
    let createCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/foods/capabilities") {
          return json({ barcode_lookup_enabled: false });
        }
        if (url === "/api/v1/foods/custom") {
          createCalls += 1;
          return pendingCreate.promise;
        }
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    renderDialog();

    fireEvent.click(screen.getByRole("tab", { name: /custom food/i }));
    fireEvent.change(screen.getByLabelText(/food name/i), { target: { value: "My tofu" } });
    fireEvent.change(screen.getByLabelText(/^calories$/i), { target: { value: "165" } });
    fireEvent.change(screen.getByLabelText(/protein/i), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText(/carbohydrate/i), { target: { value: "20" } });
    fireEvent.change(screen.getByLabelText(/^fat/i), { target: { value: "5" } });
    const form = screen.getByRole("button", { name: /save private food/i }).closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);
    fireEvent.submit(form as HTMLFormElement);
    expect(createCalls).toBe(1);

    pendingCreate.resolve(
      json({
        id: "e650490a-068a-444b-83ff-c4d1cc18158e",
        source: "custom",
        source_id: "e650490a-068a-444b-83ff-c4d1cc18158e",
        name: "My tofu",
        nutrients: {},
        portions: [],
        private: true,
      }, 201),
    );
    expect(await screen.findByRole("heading", { name: /log my tofu/i })).toBeVisible();
    expect(screen.getByLabelText(/measure/i)).toHaveValue("g");
    expect(screen.getByLabelText(/amount in grams/i)).toHaveValue(100);
  });

  it("renders every attribution kind and drops unsafe source links", () => {
    render(
      <div>
        <FoodAttributionLine source="custom" />
        <FoodAttributionLine source="usda" />
        <FoodAttributionLine
          source="community"
          attribution={{
            contributed_by: "Sam",
            source_license: "CC-BY-4.0",
            source_uri: "javascript:alert(1)",
          }}
        />
        <FoodAttributionLine
          source="community"
          attribution={{ license: "MIT", source_uri: "https://example.test/food" }}
        />
        <FoodAttributionLine
          source="openfoodfacts"
          attribution={{ attribution_text: "OFF community", source_url: "not a URL" }}
        />
      </div>,
    );

    expect(screen.getByText(/private to your account/i)).toBeVisible();
    expect(screen.getByText(/USDA · CC0 1.0/i)).toBeVisible();
    expect(screen.getByText(/contributed by Sam · Community food · CC-BY-4.0/i)).toBeVisible();
    expect(screen.getByText(/OFF community · ODbL 1.0/i)).toBeVisible();
    const links = screen.getAllByRole("link", { name: "Source" });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "https://example.test/food");
  });
});
