export type HealthCopyRule = { label: string; pattern: RegExp };

export const prohibitedHealthCopy: HealthCopyRule[] = [
  { label: "habit mechanics", pattern: /\bstreaks?\b/i },
  {
    label: "evaluative feedback",
    pattern: /\b(?:great job|well done|you (?:failed|succeeded)|your (?:health )?score)\b/i,
  },
  {
    label: "shame framing",
    pattern: /\b(?:guilt(?:y)?|sham(?:e|ing)|cheat (?:day|meal))\b/i,
  },
  { label: "food moralising", pattern: /\b(?:good|bad|clean|dirty) foods?\b/i },
  {
    label: "target judgement",
    pattern:
      /\b(?:over|under) (?:your )?(?:calorie |macro )?target\b|\bcalories? remaining\b|\byou went (?:over|under)\b/i,
  },
  {
    label: "exercise compensation",
    pattern:
      /\b(?:burn (?:it|that|this) off|earn(?:ed)? (?:food|a meal)|deserve(?:d)? (?:food|a meal))\b/i,
  },
  {
    label: "automatic coaching",
    pattern: /\b(?:you should|you need to|we recommend|aim to|increase your|decrease your)\b/i,
  },
  {
    label: "medical interpretation",
    pattern: /\b(?:your bmi|bmi says|obese|overweight|underweight|diagnos(?:e|ed|is))\b/i,
  },
  {
    label: "social comparison",
    pattern: /\b(?:leaderboard|ranked against|better than other users|top \d+%)\b/i,
  },
  {
    label: "fasting optimisation",
    pattern: /\b(?:fasting window|start (?:a )?fast|end (?:a )?fast)\b/i,
  },
];

export function containsProhibitedHealthCopy(message: string): boolean {
  return prohibitedHealthCopy.some(({ pattern }) => pattern.test(message));
}

export function reviewedApiErrorMessage(message: string): string {
  return containsProhibitedHealthCopy(message)
    ? "That request could not be completed. Please try again."
    : message;
}
