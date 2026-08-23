import type { TargetResponse as TransportTarget } from "@/lib/generated/client/types.gen";
import type { Target } from "@/lib/types";

export function target(value: TransportTarget): Target {
  return {
    id: value.id,
    day_type: value.day_type,
    kcal: value.kcal,
    protein_g: value.protein_g,
    carb_g: value.carb_g,
    fat_g: value.fat_g,
    active_from: value.active_from,
    active_until: value.active_until,
  };
}
