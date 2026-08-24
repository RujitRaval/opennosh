# Visual regression contract

This lane protects the reviewed Living Commons and unchanged Tracker compositions with deterministic
Playwright screenshots. It is a pixel gate, not a replacement for semantic, accessibility, focus,
data-contract, motion-budget, or live design review.

## Pinned environment

- Runtime: `mcr.microsoft.com/playwright@sha256:dcc5531e97840b9b5e794f2814476b21571c5124a3fca2267d73041f56e7580e`
- Browser package: Playwright `1.62.1`, locked by `package-lock.json`
- Locale and timezone: `en-US`, `UTC`
- Browser time: `2026-08-24T16:00:00.000Z`
- Fonts: committed files from `web/assets/fonts/v1/`; capture waits for `document.fonts.ready`
- Data: committed public, food-record, contribution, and Tracker fixtures with stable identifiers
- Motion: the decoration kill switch removes incidental motion; focused assertions still prove
  reduced-motion input and accepted-versus-non-live activity semantics

## Matrix

The 29 committed PNGs cover 320 px reflow, representative mobile, tablet, desktop, and wide desktop;
all five public proof states; Explore entry; the trust-first food record; every contribution stage;
validation and duplicate repair; all six logo colorways; public loading and unavailable boundaries;
the Tracker daily log and catalogue results; pseudo-localized long text; forced colors; keyboard focus;
reduced motion; and mobile safe-area actions.

Screenshots use no pixel masks. Only incidental transitions, carets, the unfocused skip link, and the
test server's development indicator are stabilized. Required content remains present in the DOM and
is also covered by behavioral tests.

## Run and update

CI runs `npm run check:visual-baselines` followed by `npm run test:visual` inside the pinned image.
Run the same check locally with Docker:

```sh
docker run --rm --ipc=host \
  -v "$PWD:/work" \
  -v opennosh-visual-node-modules:/work/web/node_modules \
  -w /work/web \
  mcr.microsoft.com/playwright@sha256:dcc5531e97840b9b5e794f2814476b21571c5124a3fca2267d73041f56e7580e \
  bash -lc 'npm ci && npm run check:visual-baselines && npm run test:visual'
```

Baseline updates are review events, not snapshot housekeeping:

1. Run the visual lane with `--update-snapshots` in the pinned image.
2. Inspect every expected/actual/diff rendering in `test-results/visual` and the HTML report.
3. Record the reason, the linked `DESIGN.md` decision, and reviewer acknowledgement:

   ```sh
   VISUAL_BASELINE_REASON='why the pixels changed' \
   VISUAL_BASELINE_DESIGN_DECISION='DESIGN.md: Visual regression' \
   VISUAL_BASELINE_REVIEWER='reviewer name and PR review link' \
   node scripts/check_visual_baselines.mjs --write
   ```

4. Re-run `npm run check:visual-baselines` and `npm run test:visual` without update mode.

Logo and Tracker changes are release-blocking. Their baselines may move only after explicit reviewer
approval. A pull request must attach the rendered diff artifact and explain the relevant design
decision; accepting an unreviewed `--update-snapshots` result is not approval.
