# Design System: opennosh

opennosh is an open food-data commons with a self-hosted nutrition and strength tracker attached. This document is the source of truth for public-site, tracker, documentation, and campaign UI decisions.

## Product Context

- **What this is:** A public, versioned, CC0 food-data commons that people can search, verify, improve, download, and reuse. The tracker proves the data works in a real product and remains a secondary self-hosting utility.
- **Who it is for:** People seeking accountable food information, contributors documenting regional and home-cooked foods, developers reusing open data, and self-hosters who want a private tracker.
- **Space:** Open data, food knowledge, nutrition software, civic technology, and open-source infrastructure.
- **Project type:** Public editorial website, data explorer, contribution portal, developer surface, and self-hosted web application.
- **Primary promise:** Food data belongs to everyone.
- **Supporting line:** Search it. Verify it. Add what is missing. Reuse it anywhere.

## Brand Foundation

### Brand idea: The Living Commons

opennosh should feel like collective action people can join, not a database behind glass. The interface makes participation and accumulation visible. Movement comes from real searches, foods, sources, packs, contributors, and accepted changes rather than decorative animation alone.

### Brand personality

- Open, civic, optimistic, and useful.
- Culturally curious without treating regional food as novelty.
- Evidence-based without sounding clinical or bureaucratic.
- Energetic without guilt, streak pressure, diet culture, or social comparison.
- Confident enough to expose provenance, uncertainty, and missing data.

### Voice

- Use active, plain language: “Search the commons,” “See the source,” and “Help add this food.”
- Treat missing data as an invitation, not an error: “We do not have this preparation yet. Help begin the record.”
- Explain uncertainty directly: “Nutrition varies across household and restaurant preparations.”
- Prefer “food,” “record,” “source,” “preparation,” “portion,” and “commons.”
- Avoid “clean food,” “bad food,” “cheat,” “crush your goals,” “guilt-free,” shaming, streaks, and competitive health language.

## Aesthetic Direction

- **Direction:** The Living Commons.
- **Aesthetic:** Editorial movement with public-institution credibility.
- **Decoration:** Expressive on the public website; restrained inside records, forms, and tracker workflows.
- **Mood:** Monumental, participatory, culturally open, accountable, and alive.
- **References:** [Awwwards](https://www.awwwards.com/), [Run Rob Run](https://www.runrobrun.com/), [Produx](https://www.produx.design/), [Lama Lama](https://lamalama.com/), [Monolog](https://bymonolog.com/), [Indigo Laboratory](https://indigo-laboratory.it/), and [Tresmares Capital](https://www.tresmarescapital.com/en/).

### Creative rule

Use stages, fields, bands, ledgers, and chapters. Do not turn the public homepage into a centered stack of small, uniform cards. Cards are allowed when they clarify a bounded object such as a form step, source notice, mobile control, or account setting.

### Hybrid discipline

- Public storytelling may use asymmetry, overlap, oversized type, and full-width color transitions.
- Food records, provenance, API documentation, and contribution forms return to a disciplined grid.
- Navigation, search, focus order, labels, and critical actions remain familiar even when the composition is experimental.

## Identity

### Wordmark

The lowercase `opennosh` wordmark is the primary identity. “open” and “nosh” must remain visibly distinct in every colorway. The abstract open ring symbolizes a commons with room for another contribution. Do not introduce fork, spoon, leaf, flame, heart, calorie, or generic wellness iconography.

### Approved colorways

| Intended surface | Ring + “open” | “nosh” | Use |
|---|---|---|---|
| Rice Paper `#F4F0E6` | Commons Ink `#12120F` | Signal Tomato `#F04E35` | Primary header and light surfaces |
| Commons Ink `#12120F` | Rice Paper `#F4F0E6` | Field Acid `#D7F34C` | Dark and inverse surfaces |
| Signal Tomato `#F04E35` | Commons Ink `#12120F` | Rice Paper `#F4F0E6` | Footer and campaign surfaces |
| Field Acid `#D7F34C` | Commons Ink `#12120F` | Dataset Indigo `#5848E8` | Campaign and contribution surfaces |
| One-color light | Rice Paper `#F4F0E6` | Rice Paper `#F4F0E6` | Dark photography or constrained reproduction |
| One-color dark | Commons Ink `#12120F` | Commons Ink `#12120F` | Print, embossing, and constrained reproduction |

Never reuse the Signal Tomato “nosh” accent on a Signal Tomato surface. Logo components select a colorway from their surface instead of inheriting one global accent rule.

### Logo usage

- Preserve the relative ring size, word spacing, and lowercase construction.
- Minimum digital width: 112 px for the wordmark; use the ring alone below that size.
- Clear space: at least half the ring diameter on every side.
- Do not stretch, rotate, outline, shadow, gradient-fill, or recolor individual letters outside the approved combinations.
- Production assets must include outlined SVG versions of each colorway plus one-color light and dark variants.

## Typography

### Families

- **Display and hero:** Archivo Variable. Its width and weight axes provide campaign-scale energy without unrelated headline fonts.
- **Body and interface:** Source Sans 3. It keeps explanations, forms, and health-related information calm and readable.
- **Data, labels, tables, and code:** IBM Plex Mono with tabular numerals for quantities, timestamps, versions, identifiers, and nutrition values.
- **Multilingual fallback:** Noto Sans for content in scripts not covered by the primary families. Never transliterate a food name merely to fit the brand font.

Self-host variable WOFF2 files in production, preload only the above-fold weights, and use `font-display: swap`. External font CDNs are prototype-only.

### Scale

| Role | Desktop | Mobile | Notes |
|---|---:|---:|---|
| Movement display | `clamp(96px, 11.8vw, 220px)` | `clamp(54px, 16.5vw, 88px)` | Archivo 800–900, width 75–95, line-height 0.72–0.82 |
| Section display | `clamp(72px, 10vw, 176px)` | `clamp(54px, 18vw, 96px)` | Uppercase allowed for declarations |
| Page H1 | 72–104 px | 48–64 px | Sentence case on data pages |
| H2 | 44–64 px | 34–44 px | Line-height 0.95–1.05 |
| H3 | 28–40 px | 24–32 px | Record sections |
| Lead | 24–32 px | 20–24 px | 45–55 characters per line |
| Body | 18–20 px | 17–19 px | Line-height 1.35–1.55 |
| Utility/data | 10–13 px | 10–12 px | IBM Plex Mono; uppercase only for short labels |

Tight tracking belongs only on large Archivo headlines. Body copy keeps normal tracking. Never set long instructions in uppercase or monospaced text.

## Color

### Core palette

| Token | Value | Meaning and use |
|---|---|---|
| `commons-ink` | `#12120F` | Primary text, dark stages, trusted data surfaces |
| `rice-paper` | `#F4F0E6` | Primary light surface; avoid pure white by default |
| `signal-tomato` | `#F04E35` | Participation, contribution calls, campaign energy |
| `field-acid` | `#D7F34C` | Accepted activity, highlights, movement signals |
| `dataset-indigo` | `#5848E8` | Structured data, API, provenance, commons activity |

### Semantic colors

| Token | Value | Use |
|---|---|---|
| `success` | `#176B43` | Completed validation and accepted submissions |
| `warning` | `#9A5B00` | Incomplete evidence and non-blocking review |
| `error` | `#B3261E` | Invalid input and blocking failures |
| `info` | `#3157C8` | Neutral system guidance |

Pair color with text or iconography. Never encode provenance, validation, or nutrition meaning through color alone.

### Dark mode

- Swap Rice Paper and Commons Ink as surface and text roles.
- Keep Tomato and Acid for short accents, not paragraphs.
- Reduce saturation of large Indigo surfaces by 10–15 percent when needed for comfortable reading.
- Dark mode is a surface redesign, not blanket inversion.

## Spacing

- **Base unit:** 8 px.
- **Density:** Spacious for public storytelling, comfortable for records and forms, compact only for dense tables.
- **Scale:** `2xs 2`, `xs 4`, `sm 8`, `md 16`, `lg 24`, `xl 32`, `2xl 48`, `3xl 64`, `4xl 96`, `5xl 128`, `stage 160–224` px.
- Public stages normally use `clamp(104px, 13vw, 208px)` vertical padding.

## Layout

- **Approach:** Hybrid creative-editorial and grid-disciplined.
- **Desktop:** 12 columns, 24–32 px gutters.
- **Tablet:** 8 columns, 20–24 px gutters.
- **Mobile:** 4 columns, 16 px gutters.
- **Page margin:** `clamp(20px, 3.2vw, 60px)`.
- **Wide canvas:** Up to 1600 px; visual fields and color stages may be full bleed.
- **Reading width:** 60–72 characters.
- **Radius:** 0 px by default, 2–4 px for compact controls, full only for toggles, circular actions, and semantic pills.

### Page architecture

1. Home and movement manifesto.
2. Explore and anonymous public search.
3. Accountable food record.
4. Packs, cuisines, and locales.
5. Contribution guide and submission flow.
6. Data downloads, schema, API, and licensing.
7. About the commons and governance.
8. Self-hosting and tracker documentation.

### Homepage sequence

1. Viewport-scale declaration: “Food data belongs to everyone.”
2. Public search terminal with culturally varied examples.
3. Live commons ledger and contribution activity.
4. One complete record with nutrition, context, provenance, version, license, and contributor.
5. A failed search transforming into “Help add this food.”
6. Dataset, API, schema, and CC0 reuse surfaces.
7. Self-hosting as proof of openness, not the primary pitch.

## Core Interface Patterns

### Search

- Anonymous by default.
- Use a full-width public search field, not a small centered card.
- Preserve the exact query when no match exists.
- A failed search offers contribution without pretending a result exists.

### Food records

Every public record exposes nutrients, portions, cuisine or locale, provenance, license, pack, version, source, and contributor. Distinguish reference preparations from universal claims. Put uncertainty beside the data it qualifies.

### Measurement display

- Accept grams, ounces, and pounds for food or batch inputs.
- Store and calculate nutrition in canonical grams.
- Preserve the originally entered unit.
- Metric and US controls change quantities and portions; macronutrients remain in grams.
- Review screens show entered and canonical values when conversion occurred.
- Body and workout measurements default to pounds/inches in US mode and kilograms/centimeters in Metric mode.

### Contribution flow

Use three chapters across five stages: begin the record with evidence and details, verify the claim
with duplicate checking and provenance, then send the exact proposal to review. Explain why evidence
is requested. Preserve contributor intent, source terms, public credit, and original units beside
canonical grams. Device and server drafts are operational proposals, not accepted food data; Git
and verified releases remain the source of truth for the published commons.

### Provenance

Provenance is a first-class interface, not footer metadata. Use chronological ledger rows for source creation, nutrient calculation, peer verification, pack publication, and revisions.

## Contribution Activity Visualization

### Prototype

The approved mock uses CSS-built illustrative dots and hardcoded sample values. It must carry the visible label `ILLUSTRATIVE DATA`. Sample counts must never be styled or described as production facts.

### Production

- Use a rolling 24-hour window.
- One event represents an accepted new food, source addition, verified portion, or published pack.
- Position represents the food or pack’s broad locale, never a contributor’s precise location.
- Aggregate to country or broad region when the locale is too specific.
- Use accepted repository or publication events. Never animate pending or rejected submissions as accepted.
- With no qualifying events, show an honest quiet state: “No accepted changes in the last 24 hours.” Never fabricate motion or counts.
- Provide a text summary and legend so the visualization is not required to understand the data.

Suggested fields: `event_type`, `food_or_pack_id`, `food_locale`, `accepted_at`, `source_commit`, and `public_contributor_credit`. Precise IP, device, or contributor geolocation does not belong here.

## Imagery and Graphic Language

- Prefer real records, source fragments, ingredient scans, pack artifacts, and contributor-approved documentary imagery.
- Use food names, locales, versions, and activity as visual material.
- Avoid stock-food photography, glossy wellness imagery, floating app mockups, and unrelated decorative 3D objects.
- The open ring, ledger lines, grid fields, and contribution pulses form the repeatable graphic language.

## Motion

- **Approach:** Expressive for public storytelling; intentional for tracker and explorer workflows.
- **Easing:** `cubic-bezier(.16, 1, .3, 1)` for entrances and large moves; ease-out for controls.
- **Duration:** micro 80–120 ms, short 160–240 ms, medium 300–450 ms, long 600–900 ms.
- Animate transform and opacity where possible.
- Search may expand into results; accepted contributions may create a restrained pulse; headlines may change width or weight between chapters.
- Do not use splash loaders, scroll hijacking, forced parallax, autoplay audio, or motion that blocks content.
- Respect `prefers-reduced-motion` by removing loops and making state changes immediate.
- The public document starts with `data-motion="off"`; headings, navigation, status, and actions are
  complete and visible before JavaScript runs. Motion never reveals required content.
- The optional controller loads after browser readiness only when reduced-motion, data-saver, slow
  network, low-power, and the build-time decoration kill switch allow it.
- At most two visible motion regions may run. Hidden tabs and offscreen regions pause, and frame or
  long-task budget breaches disable decoration for the remainder of the page visit.
- Motion source is limited to 12 KB gzip, the attributed public design delta to 45 KB gzip, motion
  tasks to 50 ms, and visible-motion p95 frames to less than 20 ms.
- `make motion-performance-check` is the executable contract. Its desktop/mobile Core Web Vitals
  gates are LCP <=2.5 s, INP <=200 ms, and CLS <=0.1.

## Accessibility

- Target WCAG 2.2 AA for text, controls, focus, forms, and meaningful graphics.
- Validate every logo colorway and text pairing on its intended surface.
- Maintain visible keyboard focus and logical document order through overlapping compositions.
- Never hide essential content behind hover, motion, canvas, or color.
- Provide labels, legends, tables, or summaries for data visualizations.
- Minimum touch target: 44 by 44 px.

## Responsive Behavior

- Preserve hierarchy on mobile rather than reducing the page to unrelated cards.
- Decorative fields and rings may crop beyond the viewport; essential words, values, controls, and provenance may not.
- Collapse dense ledgers into two-column rows with secondary metadata under titles.
- Stack nutrition values predictably and keep unit controls near the serving.
- Convert multi-column contribution and brand-specimen layouts to one readable column.

## Reference Prototype

The approved interactive artifact lives outside the repository at:

`~/.gstack/projects/RujitRaval-opennosh/designs/living-commons/`

It demonstrates the homepage, search, activity model, food record, measurement toggle, contribution path, API and self-hosting surfaces, responsive behavior, dark mode, and logo colorways. This document, not the prototype implementation, is the durable source of truth.

## Decisions Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-08-22 | Adopt “The Living Commons” | Collective activity and accountable data create the movement feeling. |
| 2026-08-22 | Use hybrid editorial and disciplined-data layouts | The brand can be expressive without making records hard to trust or use. |
| 2026-08-22 | Adopt Archivo, Source Sans 3, and IBM Plex Mono | The roles separate campaign expression, readable UI copy, and accountable data. |
| 2026-08-22 | Approve the five-color core palette | Ink and Paper establish trust; Tomato and Acid signal participation; Indigo identifies data. |
| 2026-08-22 | Require surface-specific logo colorways | Both “open” and “nosh” remain visible on every background. |
| 2026-08-22 | Treat contribution activity as real or clearly illustrative | The commons cannot build trust with fabricated live activity. |
| 2026-08-22 | Preserve original units and calculate nutrition in grams | US display coexists with a stable canonical model. |
| 2026-08-22 | Keep health language neutral | opennosh reports data without guilt, competition, or coercive engagement. |

## Production delivery contract

- `web/app/(public)/[language]/public.css` is the runtime source for public color, spacing, grid, motion, and responsive tokens.
- `web/assets/fonts/` contains the self-hosted production font subsets and their retained licenses; the public root alone imports them through `web/lib/public-fonts.ts`.
- `web/public/brand/` contains outlined SVG wordmarks for every approved surface. Components select a named surface through `web/lib/brand-assets.ts`; they never recolor one generic asset.
- `web/lib/public-navigation.ts` and `web/lib/routes.ts` are the typed source of truth for the four public hubs, interface-language fallback, feature-gated child links, and Tracker routes.
- The public and tracker route groups are independent document roots. Public brand CSS and fonts must not enter the tracker bundle.
- Proposed changes to identity, type roles, core palette, voice, or measurement behavior require updating this contract and its tests in the same change.
