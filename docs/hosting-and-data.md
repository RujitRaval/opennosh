# Hosting and your data

This describes the opennosh.org owner-operated beta. A self-hosted instance is controlled by its
own operator and may use different hosting, retention, and enabled features.

## Operator and contact

Rujit Raval operates the hosted service, reviews food suggestions, and handles incidents and data
questions. Contact `support@opennosh.org`. Food suggestions are reviewed weekly when capacity allows;
support is best effort and has no guaranteed response time. Report suspected vulnerabilities through
[GitHub private vulnerability reporting](https://github.com/RujitRaval/opennosh/security/advisories/new),
not a public issue. Do not send passwords, recovery codes, or private health exports.

## What is stored and where

The application and PostgreSQL account database run on Render in Ohio, United States. Cloudflare
provides domain, public delivery, email forwarding, and public artifact infrastructure. GitHub hosts
source, proposed changes, and public contribution history. Infrastructure providers process requests
and operational logs needed to deliver the service.

An account stores an email address, password hash, recovery-code hash, sessions, preferences, and
the Tracker records you enter. Private Tracker records are scoped to your account and are excluded
from the public food datasets. The hosted service is not end-to-end encrypted; its operator and
infrastructure providers can process stored information for operation, support, and recovery.

Session and security cookies authenticate requests and protect against cross-site requests.
Browser storage can retain language preferences and unfinished food contribution drafts. Clear site
data when leaving a shared device. Public search does not require an account.

## Retention, export, and deletion

Private records remain in the active database until you remove them or delete the account. Export
your private data through the Tracker before deleting the account in Account settings. Deletion
removes the account's private Tracker records from the active database and revokes its sessions.
It does not retract public contributions, review decisions, signed releases, receipts, or Git history.
Contributor-credit removal requests follow [the contribution policy](../CONTRIBUTING.md).

Existing database backups are not rewritten by account deletion. Provider backup and log retention
must be checked against the current service plan; the dated production verification and restore
procedure are recorded in [announcement readiness](operations/announcement-readiness.md). This beta
does not promise instantaneous erasure from backup copies. Recovery must account for deletions made
after the chosen backup before reopening the restored service.

## Recovery and service availability

Save the one-time recovery code shown during registration. Recovery requires your email, that code,
and a new password; it revokes earlier sessions and replaces the code. There is no email password
reset flow, and support cannot retrieve a lost recovery code. Keep your own private exports if you
need a copy independent of this beta service.

The owner monitors the service and uses the existing deployment and backup controls. The beta has
no availability guarantee. During an incident, report the time and a non-sensitive description to
support. Never include authentication credentials or another person's records.

## Public contributions

Only submit information you intend to make public and have permission to share. The current pilot
stores bounded public citation metadata rather than uploaded source images. Owner-approved records
are labeled as owner approved; no independent review is implied. See the
[food suggestion workflow](../CONTRIBUTING.md#food-suggestions-during-the-beta).
