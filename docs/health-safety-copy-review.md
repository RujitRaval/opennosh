# Health-safety copy review

Issue [#23](https://github.com/RujitRaval/opennosh/issues/23) requires a human decision because automated tests can detect known bad patterns but cannot judge tone, pressure, or emotional impact. This inventory covers every target, food, body-metric, and workout state currently exposed by the web application or its target-setting API.

## Review standard

Approve a state only when it:

- reports recorded values without praise, blame, scores, streaks, or moral labels;
- does not infer health, diagnosis, body status, or a recommended action;
- treats values on either side of a target with the same visual and verbal tone;
- gives a clear next action for loading, empty, validation, and error states; and
- keeps target choice with the user, including a deliberate settings-level confirmation below the configured calorie floor.

The automated copy gate parses user-facing string literals and composed static copy in production web source for prohibited patterns covering streaks, praise or blame, scores, guilt and shame, food moralising, target judgement, compensatory exercise, automatic coaching, BMI or diagnosis language, social comparison, and fasting optimisation. API error details pass through the same rules before display and fall back to neutral retry copy when needed. Self-tests prove every prohibited-copy rule detects a representative violation while allowing a neutral near-miss, and a rendering snapshot verifies that intake below and above a target uses the same neutral text and DOM structure.

## Screen and state inventory

| ID | Surface and states | Current interaction or copy | Automated review | Human decision |
|---|---|---|---|---|
| AUTH-01 | Daily-log and trends startup | “Opening your daily log…” and “Opening trends…” | Pass: status only | Approved |
| AUTH-02 | Sign in, account creation, validation, and request error | Explains the action and preserves the user’s chosen mode; errors say what failed without blame | Pass | Approved |
| AUTH-03 | Expired session, sign-out success, and sign-out error | “Your session ended. Sign in again to continue.” and neutral request status | Pass | Approved |
| LOG-01 | Date and training/rest selection | Controls identify the viewed date and user-selected day type; no type is recommended | Pass | Approved |
| LOG-02 | Daily totals with no target | Numeric totals plus “No target set” | Pass | Approved |
| LOG-03 | Daily totals below, equal to, or above a target | Always “recorded value of target value”; progress is capped visually and no remaining/over/under judgement is added | Pass: rendering snapshot | Approved |
| LOG-04 | Empty day | “Nothing logged for this day” with an Add food action | Pass | Approved |
| LOG-05 | Loaded meal groups and delete action | Food identity, quantity, nutrients, and a factual delete action | Pass | Approved |
| LOG-06 | Loading, first-100 notice, load failure, and delete failure | Status plus retry or another recoverable action; no failure is attributed to the user | Pass | Approved |
| FOOD-01 | Search initial, short query, loading, empty, result, and API-error states | Describes the catalogue state and how to search or create a private food | Pass | Approved |
| FOOD-02 | Food selection, quantity entry, add success, and add failure | Names the selected food and requested quantity; success confirms the record without praise | Pass | Approved |
| FOOD-03 | Barcode disabled, input, lookup, missing product, upstream error, and result | Barcode is opt-in and factual; local logging remains available | Pass | Approved |
| FOOD-04 | Private custom-food entry, validation, save, and success | Uses factual nutrient labels, marks the food private, and does not rate the food | Pass | Approved |
| TREND-01 | Trends loading, date range, timezone note, error, and retry | Describes recorded history and date boundaries without a score or recommendation | Pass | Approved |
| TREND-02 | Nutrition empty, single-point, and multi-point states | Empty and single-point copy describes available records; chart and table report values only | Pass | Approved |
| TREND-03 | Body-metric empty, single-point, and multi-point states | Separates metric types and units; no BMI category, diagnosis, or body judgement | Pass | Approved |
| TREND-04 | Strength empty, single-point, and multi-point states | Reports compatible numeric volume and explains excluded load types without grading performance | Pass | Approved |
| TARGET-01 | Target absent, present, and user-selected training/rest variant | Targets are explicitly user-chosen and are never calculated or prescribed by opennosh | Pass | Approved |
| TARGET-02 | Below-floor target without confirmation | “This value is below the configured safety floor of 1200.00 kcal. Confirm this specific target in settings to save the value you entered.” | Pass: exact-copy test | Approved |
| TARGET-03 | Below-floor target with deliberate confirmation | Stores that the user confirmed the specific value and the floor that applied | Pass | Approved |
| TARGET-04 | Configured floor raised after an older target was saved | Unconfirmed older values below the new floor require review and are not resolved as active targets | Pass: API integration coverage | Approved |
| BODY-01 | Body-metric entry | No body-metric entry screen exists in the current web app; the owner-scoped API has factual metric and unit fields | Not a current screen | Approved with follow-up |
| WORKOUT-01 | Workout and set entry | No workout-entry screen exists in the current web app; the owner-scoped API records exercise, reps, load, and notes without scoring | Not a current screen | Approved with follow-up |
| SETTINGS-01 | Safety resource pointer | No settings screen exists yet. Before that surface ships, add the required dismissible National Alliance for Eating Disorders treatment and helpline pointer. | Follow-up required | Approved with follow-up |

## External resource verification

Verified on 2026-08-21 that the National Alliance for Eating Disorders [Find Treatment](https://www.allianceforeatingdisorders.com/find-treatment/) page is live and currently publishes its treatment finder, helpline, hours, email referral route, and crisis-line distinction. The product should link to the maintained page rather than copying contact details that can become stale.

## Human review record

- Reviewer: Rujit Raval
- Review date: 2026-08-21
- Reviewed commit: `4dbfb5e5669ac4cdc28845bc18e4f7217a679a0f`
- Decision: Approved with the documented follow-ups
- Approved inventory IDs: All current inventory IDs (`AUTH-01` through `SETTINGS-01`)
- Follow-ups: `SETTINGS-01` must be implemented with the future settings surface; re-audit `BODY-01` and `WORKOUT-01` when their web entry screens are added.

This approval applies to the user-facing copy at the reviewed commit. The approval-record update itself does not change that copy. Any later change to user-facing copy requires this matrix and the automated snapshots to be reviewed again.
