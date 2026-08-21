export type AuthenticatedUser = {
  id: string;
  email: string;
};
export type SessionResponse = {
  user: AuthenticatedUser;
  csrf_token: string;
};

export type FoodSource = "usda" | "community" | "openfoodfacts" | "custom";
export type CatalogueFoodSource = "usda" | "community";

export type FoodAttribution = {
  source?: FoodSource;
  license?: string;
  source_uri?: string | null;
  source_url?: string;
  source_license?: string | null;
  database_license?: string;
  contents_license?: string;
  attribution_text?: string;
  contributed_by?: string | null;
};

export type FoodSearchItem = {
  id: string;
  source: CatalogueFoodSource;
  source_id: string;
  name: string;
  name_local: string | null;
  category: string | null;
  attribution: FoodAttribution;
};

export type FoodSearchResponse = {
  items: FoodSearchItem[];
  limit: number;
  offset: number;
  has_more: boolean;
};

export type HouseholdPortion = {
  name: string;
  grams: string;
};

export type FoodDetail = FoodSearchItem & {
  nutrients: Record<string, unknown>;
  portions: HouseholdPortion[];
};

export type BarcodeFood = Omit<FoodDetail, "source" | "name_local" | "category"> & {
  source: "openfoodfacts";
  barcode: string;
  brand: string | null;
  cached: boolean;
};

export type CustomFood = Omit<
  FoodDetail,
  "source" | "name_local" | "category" | "attribution"
> & {
  source: "custom";
  private: true;
};

export type FoodCapabilities = {
  barcode_lookup_enabled: boolean;
};

export type LogEntry = {
  id: string;
  logged_at: string;
  meal_slot: string;
  food: {
    source: string;
    source_id: string;
    name: string;
  };
  quantity: {
    amount: string;
    unit: "g" | "ml" | "portion";
    portion_name: string | null;
  };
  snapshot: {
    basis: "computed";
    grams: string;
    nutrients: Record<string, string>;
  };
};

export type LogEntryListResponse = {
  day: string;
  timezone: string;
  items: LogEntry[];
  limit: number;
  offset: number;
  has_more: boolean;
};

export type DailyTotals = {
  day: string;
  timezone: string;
  entry_count: number;
  grams: string;
  nutrients: Record<string, string>;
};

export type Target = {
  id: string;
  day_type: "training" | "rest";
  kcal: string;
  protein_g: string;
  carb_g: string;
  fat_g: string;
  active_from: string;
  active_until: string | null;
};
