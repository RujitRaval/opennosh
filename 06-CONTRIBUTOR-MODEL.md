# 06 — Contributor model

Historical strategy document retained for context. It is not an implementation input.

---

## The sequencing that actually works

From the earlier research: roughly 60% of open-source maintainers are unpaid, nearly 60% have quit or considered quitting, and almost half are solo. Most projects run on one to three active maintainers — solo is the default, not a failure. Contributors arrive *after* adoption, never before.

So the order is:

1. **You alone, months 0–2.** Build the app. Write the four starter packs. Use it daily for your own program. Ship nothing publicly.
2. **Launch, month 3.** Public repo, `CONTRIBUTING.md`, food pack spec, four packs, working CI validator.
3. **Invite contribution, month 4+.** Only once someone other than you has actually deployed it.

Do not open `good first issue` labels on day one. GitHub's own 2026 outlook flags the AI-slop problem: auto-generated issues and PRs that increase volume without increasing quality, where incorporating the contribution takes longer than writing it yourself. An empty project with open labels attracts exactly that.

## Why food packs are the right contribution unit

A contribution unit works when a stranger can ship something useful in under two hours without talking to you. Food packs qualify:

- **No codebase knowledge required.** It's YAML. A dietitian or a home cook who can use git can contribute.
- **Self-validating.** CI catches the arithmetic. Review is judgment only.
- **Intrinsically motivated.** People contribute their own cuisine because they want to log their own food. That motivation renews itself; "help with my project" does not.
- **Naturally global.** Every locale is a fresh contribution opportunity with an obvious owner.
- **Non-competitive.** Two people adding different cuisines never conflict. Code contributions do.

This is the same structural pattern that made n8n's node library, Home Assistant's integrations, and Semgrep's rule library work.

## Recognition, which matters more than it sounds

- `contributed_by` is a field on every food entry, surfaced in the opennosh UI. Someone logging thepla sees who added it.
- Per-pack maintainer credit in `pack.yaml`, rendered on the pack's page.
- An `AUTHORS.md` that lists every contributor, not just code contributors.
- A pack maintainer role: after three merged packs, a contributor gets merge rights on `packs/` only. Delegating review authority on the highest-volume, lowest-risk surface is the single best defence against your own burnout.

## Boundaries, set in writing before you need them

Write `CONTRIBUTING.md` as a boundary document, not a welcome mat. Say explicitly:

- What is accepted: food packs, exercise definitions, translations, bug fixes with tests
- What is not: feature PRs without a prior issue, refactors, dependency bumps, AI-generated bulk submissions
- Response expectations: "I review packs weekly, code PRs when I can. This is not my job."
- The 100-entry cap per PR
- The licence sign-off requirement

A `CONTRIBUTING.md` that sets limits is how you avoid the resentment cycle that ends most solo-maintained projects.

## The realistic ceiling

Be clear-eyed: consumer OSS converts to revenue at roughly 0.5–3%, and your users self-host *specifically because* they resent subscriptions. There is no hosted-tier business hiding in this.

What it is worth:
- The CC0 food dataset becomes infrastructure other apps depend on
- Public evidence you ship and maintain real software
- A community that isn't your customers, which is worth something on its own as a solo founder
- Genuine daily utility for your own training program

What it is not worth: your AARO time. The owner selected a hard two-month build cap, followed by maintenance and evaluation of the contributor thesis.

## Kill criteria

Set now, in writing, so you don't rationalise later:

**If sixty days post-launch there are fewer than ten packs from five distinct contributors, the community thesis is falsified.** At that point either keep it as a personal tool with no public obligations, or archive it honestly. Do not enter the multi-year zombie phase where you feel guilty about an issue tracker you've stopped reading.
