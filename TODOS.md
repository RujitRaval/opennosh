# TODOS

## Distribution

No open distribution items.

## Web quality

### Avoid a console error when no nutrition target is configured

**What:** Preserve the neutral “No target set” state without making the browser record the expected
`GET /api/v1/targets/resolve` `404` as a failed resource.

**Why:** The current experience works, but clean browser sessions show a console error for a normal
empty state, which makes real client failures harder for operators to spot.

**Context:** GStack QA reproduced this on the independent clean-install verification at
`b7686700eefcea4de625c633c879d51ed676f3a7`. Create an account without a target, open the daily log,
and inspect the browser console. The page correctly displays “No target set,” while the resolve request
returns `404`. Severity: Low. Category: Console.

**Effort:** S
**Priority:** P3
**Depends on:** Calorie and macro targets; accessible daily nutrition log

## Completed

### Publish the canonical public packages

**What:** Merged the tested package-release infrastructure, approved the initial npm release with
two-factor authentication, configured both trusted publishers, published PyPI from `main`, and
verified the public registry evidence.

**Why:** The project now controls both canonical registry names and gives users verified
installation paths without depending on stored publishing credentials.

**Context:** npm `opennosh 0.22.0` and PyPI `opennosh 0.22.0.0` are public. Workflow run
`32509681049` verified the merged source and published through OIDC; `docs/package-operations.md`
records the exact artifact hashes, install commands, license files, source links, and release
controls.

**Effort:** S
**Priority:** P1
**Depends on:** Verify public release identity and security controls

**Completed:** v0.22.0.1 (2026-08-21)

### Reserve and secure the public domain

**What:** Registered `opennosh.org`, connected the apex and `www` hostnames to the public repository, enabled DNSSEC and privacy safeguards, and configured `support@opennosh.org` with free inbound forwarding.

**Why:** The project now has a stable public address and a no-cost contact channel without committing to a hosted mailbox subscription.

**Context:** Cloudflare provides DNS, the permanent HTTPS redirect, registrar auto-renew, WHOIS redaction, and Email Routing. `docs/domain-operations.md` records the non-secret configuration and verification evidence.

**Effort:** S
**Priority:** P1
**Depends on:** Verify public release identity and security controls

**Completed:** v0.21.0.2 (2026-08-21)

### Verify public release identity and security controls

**What:** Verified npm, PyPI, and preferred-domain availability for `opennosh`, completed a clean final secret scan, made the repository public, and enabled GitHub Private Vulnerability Reporting.

**Why:** Public distribution now has a verified project identity and a private security-reporting path.

**Context:** The 2026-08-21 checks covered all reachable Git refs and GitHub metadata. Package names remain unreserved until publication; `opennosh.org` was subsequently registered and configured.

**Effort:** S
**Priority:** P1
**Depends on:** Public launch preparation

**Completed:** v0.21.0.1 (2026-08-21)

### Resolve the blocking product decisions

**What:** Selected MIT, CC0 with visible contributor credit, nutrition plus strength, the `opennosh` name, multi-user support, the v1 Open Food Facts barcode integration, wger exercise data with per-entry licensing, and a two-month build cap.

**Why:** These decisions unblock coherent licensing, architecture, naming, and issue decomposition.

**Context:** The selections are recorded in `08-PRODUCT-DECISIONS.md` and propagated through the PRD, TRD, licensing policy, contributor workflow, and repository metadata.

**Effort:** S
**Priority:** P0
**Depends on:** Explicit project-owner decisions

**Completed:** v0.0.1.0 (2026-08-19)
