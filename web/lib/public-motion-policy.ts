export type PublicMotionMode = "eligible" | "limited" | "off";

export type PublicMotionReason =
  | "eligible"
  | "kill-switch"
  | "reduced-motion"
  | "data-saver"
  | "low-power";

export interface PublicMotionEnvironment {
  decorationsEnabled: boolean;
  reducedMotion: boolean;
  saveData: boolean;
  effectiveType?: string;
  hardwareConcurrency?: number;
  deviceMemory?: number;
}

export interface PublicMotionDecision {
  mode: PublicMotionMode;
  reason: PublicMotionReason;
  loadOptionalRuntime: boolean;
}

const constrainedConnections = new Set(["slow-2g", "2g"]);

export function resolvePublicMotionPolicy(
  environment: PublicMotionEnvironment,
): PublicMotionDecision {
  if (!environment.decorationsEnabled) {
    return { mode: "off", reason: "kill-switch", loadOptionalRuntime: false };
  }
  if (environment.reducedMotion) {
    return { mode: "off", reason: "reduced-motion", loadOptionalRuntime: false };
  }
  if (environment.saveData) {
    return { mode: "off", reason: "data-saver", loadOptionalRuntime: false };
  }
  if (
    constrainedConnections.has(environment.effectiveType ?? "") ||
    (environment.hardwareConcurrency !== undefined && environment.hardwareConcurrency <= 2) ||
    (environment.deviceMemory !== undefined && environment.deviceMemory <= 2)
  ) {
    return { mode: "limited", reason: "low-power", loadOptionalRuntime: false };
  }
  return { mode: "eligible", reason: "eligible", loadOptionalRuntime: true };
}
