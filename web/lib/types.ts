export type AuthenticatedUser = {
  id: string;
  email: string;
};
export type SessionResponse = {
  user: AuthenticatedUser;
  csrf_token: string;
};

export type FoodSource = "usda" | "community";

export type FoodSearchItem = {
  id: string;
  source: FoodSource;
  source_id: string;
  name: string;
  name_local: string | null;
  category: string | null;
  attribution: {
    license: string;
    contributed_by: string | null;
  };
};

export type FoodSearchResponse = {
  items: FoodSearchItem[];
  limit: number;
  offset: number;
  has_more: boolean;
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
