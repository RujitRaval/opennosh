export type PreferredUnits = "metric" | "us";

export type AuthenticatedUser = {
  id: string;
  email: string;
  onboarding_completed: boolean;
  preferred_units: PreferredUnits;
};
export type SessionResponse = {
  user: AuthenticatedUser;
  csrf_token: string;
};
export type RegistrationResponse = SessionResponse & {
  recovery_code: string;
};
export type SessionState = {
  authenticated: boolean;
  user: AuthenticatedUser | null;
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
  pack_id?: string | null;
  pack_version?: string | null;
  provenance?: string | null;
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
  has_more: boolean;
  next_cursor: string | null;
  snapshot_id: string | null;
  snapshot_expires_at: string | null;
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

export type DailyTotalsRange = {
  from_date: string;
  to_date: string;
  timezone: string;
  items: DailyTotals[];
};

export type BodyMetric = {
  id: string;
  recorded_at: string;
  metric_type: string;
  value: string;
  unit: string;
};

export type BodyMetricListResponse = {
  from_date: string;
  to_date: string;
  items: BodyMetric[];
  limit: number;
  offset: number;
  has_more: boolean;
};

export type BodyMetricTrendResponse = {
  from_date: string;
  to_date: string;
  items: BodyMetric[];
};

export type LoadUnit = "kg" | "lb" | "machine_units" | "bodyweight" | "band" | "rpe_only";

export type WorkoutExercise = {
  id: string;
  name: string;
};

export type Workout = {
  id: string;
  performed_at: string;
  sets: Array<{
    id: string;
    exercise: WorkoutExercise;
    reps: number;
    load_value: string | null;
    load_unit: LoadUnit;
    volume: string | null;
  }>;
  volume_groups: Array<{
    exercise_id: string;
    load_unit: LoadUnit;
    volume: string;
  }>;
};

export type WorkoutListResponse = {
  from_date: string;
  to_date: string;
  items: Workout[];
  limit: number;
  offset: number;
  has_more: boolean;
};

export type WorkoutTrendPoint = {
  day: string;
  exercise_id: string;
  exercise_name: string;
  load_unit: LoadUnit;
  volume: string;
};

export type WorkoutTrendResponse = {
  from_date: string;
  to_date: string;
  items: WorkoutTrendPoint[];
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
