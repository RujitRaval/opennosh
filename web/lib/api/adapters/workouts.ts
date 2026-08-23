import type {
  WorkoutListResponse as TransportList,
  WorkoutResponse as TransportWorkout,
  WorkoutTrendResponse as TransportTrend,
} from "@/lib/generated/client/types.gen";
import type {
  Workout,
  WorkoutListResponse,
  WorkoutTrendResponse,
} from "@/lib/types";

function workout(value: TransportWorkout): Workout {
  return {
    id: value.id,
    performed_at: value.performed_at,
    sets: value.sets.map((set) => ({
      id: set.id,
      exercise: { id: set.exercise.id, name: set.exercise.name },
      reps: set.reps,
      load_value: set.load_value,
      load_unit: set.load_unit,
      volume: set.volume,
    })),
    volume_groups: value.volume_groups.map((group) => ({
      exercise_id: group.exercise_id,
      load_unit: group.load_unit,
      volume: group.volume,
    })),
  };
}

export function workoutList(value: TransportList): WorkoutListResponse {
  return {
    from_date: value.from_date,
    to_date: value.to_date,
    items: value.items.map(workout),
    limit: value.limit,
    offset: value.offset,
    has_more: value.has_more,
  };
}

export function workoutTrend(value: TransportTrend): WorkoutTrendResponse {
  return {
    from_date: value.from_date,
    to_date: value.to_date,
    items: value.items.map((item) => ({
      day: item.day,
      exercise_id: item.exercise_id,
      exercise_name: item.exercise_name,
      load_unit: item.load_unit,
      volume: item.volume,
    })),
  };
}
