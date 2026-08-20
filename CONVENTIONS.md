# opennosh conventions

## Health safety constraints

This is a calorie-tracking application. That category has a documented relationship with disordered eating, and an open-source project has the same duty of care as a commercial one.

The implementing agent must treat these as hard requirements, not suggestions:

- **No numeric target below a configurable floor without an explicit, deliberate override.** Default floor: 1,200 kcal/day. The override must be a settings-level action, not an inline nudge.
- **No streaks, no shaming, no "you went over" language.** Report numbers neutrally. A day over target renders identically in tone to a day under.
- **No goal weight validation against BMI charts**, and no unsolicited commentary on the user's target.
- **No social comparison surfaces of any kind.**
- **A dismissible resource pointer in settings** linking to the National Alliance for Eating Disorders' [current treatment-finder and helpline page](https://www.allianceforeatingdisorders.com/find-treatment/). Verify the published contact details before every release.
- **Fasting-window tracking is out of scope for v1.** It attracts a use pattern this project should not optimise for.

## Product and data constraints

- Core functionality must work without an external API after the initial food seed.
- Nutrient values are represented per 100g internally unless the food-pack specification explicitly allows per 100ml.
- Reference, community, ODbL, and private custom food data remain separate.
- Open Food Facts writes only to `foods_odbl`; no code path may copy or move those records into
  `foods_community` or include them in a CC0 export.
- Product scope and licensing must remain consistent with `08-PRODUCT-DECISIONS.md`.
- Community food packs are CC0 1.0 with visible contributor credit; third-party data retains its own license and attribution.
- v1 includes nutrition, strength training, multi-user isolation, and the opt-in Open Food Facts barcode integration.
