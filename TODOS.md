# TODOS

## Launch readiness

### Verify public release identity and security controls

**What:** Before public launch, verify npm, PyPI, and preferred domain availability for `opennosh`, and enable GitHub Private Vulnerability Reporting.

**Why:** The name and licensing architecture are selected, but external availability and the public security-reporting path must be confirmed before distribution.

**Context:** Issue #24 records the approved MIT and dataset-notice review. The remaining release checks are external availability and Private Vulnerability Reporting, not open product decisions.

**Effort:** S
**Priority:** P1
**Depends on:** Public launch preparation

## Completed

### Resolve the blocking product decisions

**What:** Selected MIT, CC0 with visible contributor credit, nutrition plus strength, the `opennosh` name, multi-user support, the v1 Open Food Facts barcode integration, wger exercise data with per-entry licensing, and a two-month build cap.

**Why:** These decisions unblock coherent licensing, architecture, naming, and issue decomposition.

**Context:** The selections are recorded in `08-PRODUCT-DECISIONS.md` and propagated through the PRD, TRD, licensing policy, contributor workflow, and repository metadata.

**Effort:** S
**Priority:** P0
**Depends on:** Explicit project-owner decisions

**Completed:** v0.0.1.0 (2026-08-19)
