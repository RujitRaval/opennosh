# TODOS

## Distribution

### Publish the canonical public packages

**What:** Merge the tested package-release infrastructure, stage and approve the initial npm
release, configure both trusted publishers, publish the PyPI release from `main`, and verify the
public registry evidence.

**Why:** Availability checks do not control a registry name. A real release gives users a verified
installation path while preventing empty name squatting.

**Context:** `docs/package-operations.md` records the exact no-secret flow. The implementation pull
request prepares the artifacts and tokenless workflow; this item remains open until both registry
pages resolve and their ownership, versions, hashes, licenses, source links, and install commands
are verified.

**Effort:** S
**Priority:** P1
**Depends on:** Verify public release identity and security controls

## Completed

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
