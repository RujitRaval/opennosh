import type {
  DailyTotalsRangeResponse as TransportDailyTotalsRange,
  DailyTotalsResponse as TransportDailyTotals,
  LogEntryListResponse as TransportLogEntryList,
  LogEntryResponse as TransportLogEntry,
} from "@/lib/generated/client/types.gen";
import type {
  DailyTotals,
  DailyTotalsRange,
  LogEntry,
  LogEntryListResponse,
} from "@/lib/types";

export function logEntry(value: TransportLogEntry): LogEntry {
  return {
    id: value.id,
    logged_at: value.logged_at,
    meal_slot: value.meal_slot,
    food: {
      source: value.food.source,
      source_id: value.food.source_id,
      name: value.food.name,
    },
    quantity: {
      amount: value.quantity.amount,
      unit: value.quantity.unit,
      portion_name: value.quantity.portion_name ?? null,
    },
    snapshot: {
      basis: "computed",
      grams: value.snapshot.grams,
      nutrients: { ...value.snapshot.nutrients },
    },
  };
}

export function logEntries(value: TransportLogEntryList): LogEntryListResponse {
  return {
    day: value.day,
    timezone: value.timezone,
    items: value.items.map(logEntry),
    limit: value.limit,
    offset: value.offset,
    has_more: value.has_more,
  };
}

export function dailyTotals(value: TransportDailyTotals): DailyTotals {
  return {
    day: value.day,
    timezone: value.timezone,
    entry_count: value.entry_count,
    grams: value.grams,
    nutrients: { ...value.nutrients },
  };
}

export function dailyTotalsRange(value: TransportDailyTotalsRange): DailyTotalsRange {
  return {
    from_date: value.from_date,
    to_date: value.to_date,
    timezone: value.timezone,
    items: value.items.map(dailyTotals),
  };
}
