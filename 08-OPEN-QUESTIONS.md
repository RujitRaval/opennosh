# 08 — Open questions

Decisions no agent should make for you. Resolve these, edit `02-PRD.md` and `03-TRD.md` to match, then run the issue pipeline.

---

## Blocking — must answer before the first issue is filed

**1. Is the app licence MIT or AGPL-3.0?**
My recommendation is MIT, argued in `04-DATA-LICENSING.md`. The counter-argument: AGPL protects against someone running a hosted version without contributing back. My view is that this threat is theoretical for a self-hosted calorie tracker, and MIT is the actual differentiator against the source-available incumbent. But it's your project and the choice is irreversible in practice.

**2. Are community food packs CC0 or CC BY 4.0?**
CC0 maximises downstream adoption, which is the moat. CC BY gets contributors named attribution wherever the data travels, which some contributors care about a lot. CC0 plus prominent in-app `contributed_by` credit is my recommendation — the credit without the legal encumbrance.

**3. Does v1 ship strength training, or nutrition only?**
The TRD includes it. The argument for cutting it: it doubles the UI surface and the food-pack story is the differentiator. The argument for keeping it: unifying nutrition and strength is a documented gap, and it's what *you* need for your own program. If you cut it, cut it now — retrofitting is worse than building it in.

**4. What's the name?** See `07-LAUNCH-PLAN.md`. Blocking because the repo, package names, and module paths all encode it.

## Important — answer before the relevant issues

**5. Multi-user or single-user for v1?**
Multi-user is in the TRD. Single-user removes auth entirely and cuts real complexity, but blocks the household use case that the competing project explicitly targets. Auth is cheap to build and expensive to retrofit; my lean is keep it.

**6. Does the OFF/barcode plugin ship in v1 at all?**
The TRD marks it `needs-human` and defers it. Barcode scanning is the single most-requested feature in this category. Shipping without it invites "no barcode = unusable" as the top HN comment. Shipping with it means proving the licence boundary works before you've proven anything else. My lean: ship without, and have a written answer ready for the thread.

**7. Where does the exercise database come from?**
wger has an open exercise database under a compatible licence. Importing it is faster than building one and is a genuine reuse story. Worth ten minutes checking the exact licence terms before assuming.

**8. What's the time cap?**
Set a real number now. My recommendation from `06-CONTRIBUTOR-MODEL.md` is two months of build, then maintenance only. Without a number, this competes with AARO indefinitely and you will resent it.

## Things I deliberately left out of the specs

- **Internationalisation of the UI.** Food packs carry locale; the interface does not. Adding i18n before you have users in other locales is premature, and it's an excellent second-wave contribution surface.
- **Meal photos.** Storage, thumbnails, and privacy for marginal logging value.
- **Any AI feature.** No photo estimation, no coaching, no insights. Every one of these adds a model dependency, an accuracy liability, and — given PRD §7 — a duty-of-care problem. If you add AI later, add it as an optional plugin with the model choice left to the deployer.
- **Fasting windows.** Excluded on health-safety grounds, per PRD §7.

## Where I'm least confident

The contributor thesis. Everything here assumes that making food packs easy will draw contributors, and that's an inference from how n8n, Home Assistant, and Semgrep grew — not evidence about this specific category. The nutrition-tracker audience may simply be less git-literate than the homelab audience I'm implicitly modelling.

That's exactly why the sixty-day, ten-packs-from-five-strangers kill criterion exists. Test the assumption cheaply rather than committing a year to it.
