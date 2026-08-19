# 07 — Launch plan

Strategy document. Does not go in the repo.

---

## Naming

The selected name is `opennosh`, used in exact lowercase form. The GitHub repository name is available and selected. Check npm, PyPI, and preferred domains before publishing packages or launching a public site.

Naming criteria that matter here: pronounceable by non-English speakers (your contributor base is global), doesn't contain "fitness" or "calorie" (those searches are dominated and commercially poisoned), and doesn't imply weight loss.

## Positioning — lead with the food data, not the tracker

This is the whole launch strategy in one line. "Another open-source MyFitnessPal alternative" is a dead post; several already exist and the audience is tired. "A CC0 food database that anyone can contribute to, with a tracker attached" is a different, fresher claim.

The proven pattern from the earlier research is the "free alternative to an expensive closed thing" framing, positioned for mass appeal and then over-delivering on the specifics. Position for the general reader, deliver for the specialist.

**Working HN title:** `Show HN: An open food database you can send a pull request to`

The tracker is the third paragraph, not the headline.

## The pre-launch checklist

Before posting anywhere:

- [ ] `docker compose up` works from a clean clone on a machine that isn't yours. Test this on a fresh VPS.
- [ ] README has a GIF in the first screen showing search → log → daily total
- [ ] The four starter packs are merged and demonstrate real quality
- [ ] `docs/foodpack-spec.md` is complete, with a copy-paste template
- [ ] CI validator runs green on a deliberately broken example PR you file against yourself
- [x] LICENSE (MIT) and the CC0 declaration for `packs/` are both present and unambiguous
- [ ] Data export works. Someone will test this first to check the promise is real.
- [ ] Health-safety copy reviewed end to end against PRD §7

## Launch sequence

**Week 0 — quiet.** Push the repo. Tell nobody. Let it sit with real commit history; a repo with one initial commit reads as vaporware.

**Week 1 — your network.** The first ~100 stars come from people you know personally. Message them directly, individually. Not a broadcast post.

**Week 2 — the specialist audiences first.** r/selfhosted and r/opensource. These are the people who will actually deploy it and file the first real bugs. Deliberately before HN, so HN traffic hits something that's been shaken out.

**Week 3 — Hacker News.** Tuesday–Thursday, 8–10am ET. Show HN. Post the GitHub link, not a landing page. Add a first comment explaining what you built and why, in plain language, no pitch. Then sit in the thread for six hours and answer everything.

Expected outcome, calibrated: HN launches average roughly 121 stars in 24 hours and 289 in a week. A front-page hit is 500–1,500. Ninety percent of HN posts are never seen at all. Plan for the median, not the tail.

**Week 4+ — the sustained channel.** Write one post per cuisine you add a pack for: "What tracking Gujarati food taught me about nutrition databases." This is the compounding channel — it's SEO for a query nobody serves, it's a contribution invitation, and it's genuinely interesting content rather than promotion.

## Second-launch material, held in reserve

Do not spend these on day one:

- Mobile app (the most-requested thing; announcing it later re-launches the project)
- The Nth locale milestone — "the food database now covers 20 cuisines"
- A comparison post benchmarking food coverage across trackers, using your own data
- Wearable ingestion, if you ever build idea #1 from the earlier list

## What not to do

- Don't post to HN twice in the same month with the same link.
- Don't buy stars, don't ask for them in the README, don't put a "star us" popup in the app.
- Don't launch on Product Hunt. Wrong audience entirely for a self-hosted developer tool.
- Don't announce a roadmap you can't staff. A public roadmap on a solo project is a public list of your unmet promises.
