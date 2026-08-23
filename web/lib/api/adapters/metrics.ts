import type {
  BodyMetricListResponse as TransportList,
  BodyMetricResponse as TransportMetric,
  BodyMetricTrendResponse as TransportTrend,
} from "@/lib/generated/client/types.gen";
import type {
  BodyMetric,
  BodyMetricListResponse,
  BodyMetricTrendResponse,
} from "@/lib/types";

function bodyMetric(value: TransportMetric): BodyMetric {
  return {
    id: value.id,
    recorded_at: value.recorded_at,
    metric_type: value.metric_type,
    value: value.value,
    unit: value.unit,
  };
}

export function bodyMetricList(value: TransportList): BodyMetricListResponse {
  return {
    from_date: value.from_date,
    to_date: value.to_date,
    items: value.items.map(bodyMetric),
    limit: value.limit,
    offset: value.offset,
    has_more: value.has_more,
  };
}

export function bodyMetricTrend(value: TransportTrend): BodyMetricTrendResponse {
  return {
    from_date: value.from_date,
    to_date: value.to_date,
    items: value.items.map(bodyMetric),
  };
}
