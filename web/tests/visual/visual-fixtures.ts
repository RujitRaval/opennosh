import { expect, type APIRequestContext, type Page } from "@playwright/test";

export const frozenNow = "2026-08-24T16:00:00.000Z";

const completeFields = {
  evidence_type: "public_document",
  source_uri: "https://example.org/evidence/rajma-masala",
  rights_acknowledged: true,
  name: "Rajma masala",
  name_local: "राजमा मसाला",
  locale: "hi-IN",
  category: "Prepared food",
  portion_description: "1 katori",
  portion_amount: "1",
  portion_unit: "serving",
  portion_grams: "180",
  energy_kcal: "229",
  protein_g: "11.2",
  fat_g: "7.4",
  carbohydrate_g: "31.8",
  ingredients: "Kidney beans, tomato, onion, ginger, garlic, and spices",
  duplicates_resolved: true,
  pack_id: "india-community-core",
  source_date: "2026-08-20",
  attribution: "Punjab Foods Collective",
  source_license: "CC0-1.0",
  review_acknowledged: true,
} as const;

export const completeContributionDraft = {
  schemaVersion: "1",
  clientDraftId: "visual-local-draft",
  fields: completeFields,
  duplicateCandidates: [],
  duplicateQuery: "Rajma masala|hi-IN",
  savedAt: frozenNow,
  saveState: "saved_on_device",
};

export const emptyContributionDraft = {
  ...completeContributionDraft,
  fields: {
    evidence_type: null,
    source_uri: "",
    rights_acknowledged: false,
    name: "",
    name_local: "",
    locale: "",
    category: "",
    portion_description: "",
    portion_amount: "",
    portion_unit: "g",
    portion_grams: "",
    energy_kcal: "",
    protein_g: "",
    fat_g: "",
    carbohydrate_g: "",
    ingredients: "",
    duplicates_resolved: false,
    pack_id: "",
    source_date: "",
    attribution: "",
    source_license: null,
    review_acknowledged: false,
  },
  duplicateCandidates: [],
  duplicateQuery: null,
};

export const repairContributionDraft = {
  ...completeContributionDraft,
  fields: { ...completeFields, duplicates_resolved: false },
  duplicateCandidates: [
    { source: "community", sourceId: "rajma-punjabi", name: "Punjabi rajma", locale: "hi-IN" },
    { source: "community", sourceId: "rajma-home", name: "Home-style rajma", locale: "en-IN" },
  ],
};

export async function installFrozenClock(page: Page) {
  await page.addInitScript((now) => {
    const NativeDate = Date;
    const fixedTime = NativeDate.parse(now);
    const FrozenDate = function (...args: unknown[]) {
      return Reflect.construct(NativeDate, args.length === 0 ? [fixedTime] : args);
    } as unknown as DateConstructor;
    Object.setPrototypeOf(FrozenDate, NativeDate);
    Object.defineProperty(FrozenDate, "prototype", { value: NativeDate.prototype });
    FrozenDate.now = () => fixedTime;
    globalThis.Date = FrozenDate;
  }, frozenNow);
}

export async function seedContribution(page: Page, draft: object) {
  await page.addInitScript((value) => {
    window.localStorage.setItem("opennosh.contribution.local.v1", JSON.stringify(value));
  }, draft);
}

export async function setCommonsState(
  request: APIRequestContext,
  state: "live" | "quiet" | "partial" | "stale" | "unavailable",
) {
  const apiPort = process.env.E2E_VISUAL_API_PORT || "8020";
  const response = await request.post(
    `http://127.0.0.1:${apiPort}/__visual/commons-state?state=${state}`,
  );
  expect(response.ok()).toBe(true);
}

export async function settleVisualPage(page: Page, surface: "public" | "tracker" = "public") {
  await expect(page.locator("html")).toHaveAttribute("data-surface", surface);
  if (surface === "public") {
    await expect(page.locator("html")).toHaveAttribute("data-motion-reason", "kill-switch");
  }
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all(
      [...document.images]
        .filter((image) => {
          const bounds = image.getBoundingClientRect();
          return !image.complete && bounds.bottom >= 0 && bounds.top <= window.innerHeight;
        })
        .map((image) => image.decode().catch(() => undefined)),
    );
  });
  await page.addStyleTag({
    content: `
      html { scroll-behavior: auto !important; }
      *, *::before, *::after { transition: none !important; caret-color: transparent !important; }
      .skip-link:not(:focus) { transform: translateY(-200%) !important; }
    `,
  });
  await page.locator("html").evaluate((element) => {
    element.dataset.visualReady = "fonts-motion-shell";
  });
  await expect(page.locator("html")).toHaveAttribute("data-visual-ready", "fonts-motion-shell");
}

export async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
}

const trackerFood = {
  id: "community:rajma-masala",
  source: "community",
  source_id: "rajma-masala",
  name: "Rajma masala",
  name_local: "राजमा मसाला",
  category: "Prepared food",
  attribution: {
    source: "community",
    license: "CC0-1.0",
    source_uri: "https://example.org/evidence/rajma-masala",
    source_license: "CC0-1.0",
    contributed_by: "Punjab Foods Collective",
    pack_id: "india-community-core",
    pack_version: "2.4.0",
    provenance: "Community reviewed",
  },
};

const trackerEntry = {
  id: "3fd6633d-c6fa-446d-a0e2-89fc3ef69b9d",
  logged_at: "2026-08-24T12:00:00Z",
  meal_slot: "Lunch",
  food: { source: "community", source_id: "rajma-masala", name: "Rajma masala" },
  quantity: { amount: "180", unit: "g", portion_name: null },
  snapshot: {
    basis: "computed",
    grams: "180.00",
    nutrients: {
      energy_kcal: "229.00",
      protein_g: "11.20",
      carbohydrate_g: "31.80",
      fat_g: "7.40",
    },
  },
};

export async function mockTrackerApi(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/v1/auth/session") {
      return route.fulfill({
        json: { id: "4c683fc5-548a-4772-a090-b26ea0951d50", email: "alex@example.com" },
      });
    }
    if (path === "/api/v1/logs/daily-totals") {
      return route.fulfill({
        json: {
          day: url.searchParams.get("day"),
          timezone: url.searchParams.get("timezone"),
          entry_count: 1,
          grams: "180.00",
          nutrients: trackerEntry.snapshot.nutrients,
        },
      });
    }
    if (path === "/api/v1/logs") {
      return route.fulfill({
        json: {
          day: url.searchParams.get("day"),
          timezone: url.searchParams.get("timezone"),
          items: [trackerEntry],
          limit: 100,
          offset: 0,
          has_more: false,
        },
      });
    }
    if (path === "/api/v1/targets/resolve") {
      return route.fulfill({
        json: {
          id: "5ff7c942-62d1-43df-8809-a76303d9a889",
          day_type: "training",
          kcal: "2200.00",
          protein_g: "160.00",
          carb_g: "240.00",
          fat_g: "70.00",
          active_from: "2026-01-01",
          active_until: null,
        },
      });
    }
    if (path === "/api/v1/foods/capabilities") {
      return route.fulfill({ json: { barcode_lookup_enabled: true } });
    }
    if (path === "/api/v1/foods/search") {
      return route.fulfill({
        json: { schema_version: "1.0", items: [trackerFood], limit: 12, offset: 0, has_more: false },
      });
    }
    return route.fulfill({ status: 404, json: { detail: `Unhandled visual fixture: ${path}` } });
  });
}
