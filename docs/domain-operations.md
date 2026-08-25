# Domain operations

This record captures the public, non-secret configuration for `opennosh.org`. It deliberately excludes Cloudflare account identifiers, registrant contact details, payment information, nameserver assignments, and the private forwarding destination.

## Current purpose

`opennosh.org` is the stable public address for the project. Until a dedicated project site is deployed, visits to the apex domain and `www` are permanently redirected to the public GitHub repository:

```text
https://github.com/RujitRaval/opennosh
```

The GitHub repository homepage metadata points back to `https://opennosh.org`, so either address leads visitors to the project.

## Cloudflare configuration

Verified on 2026-08-21:

- Registrar status is active and auto-renew is enabled.
- WHOIS contact-data redaction is enabled.
- The transfer lock is active under the new-registration hold.
- DNSSEC is enabled; registry propagation may remain pending immediately after activation.
- Proxied apex and `www` DNS records allow Cloudflare to serve the redirect without a separate origin server.
- One active 301 redirect sends all apex and `www` requests to the GitHub repository.
- Cloudflare Email Routing is enabled with its required MX and SPF records.
- `support@opennosh.org` forwards inbound mail to a verified private destination.
- The catch-all email rule remains disabled, so misspelled and unplanned addresses are not silently accepted.

Cloudflare Email Routing provides inbound forwarding only. opennosh does not currently pay for a hosted mailbox or custom-domain outbound SMTP service, and replies may therefore come from the project owner's normal mailbox.

## Public verification

The following requests were verified to return `301 Moved Permanently` with a `Location` header pointing to the GitHub repository:

```text
http://opennosh.org
https://opennosh.org
https://www.opennosh.org
```

Public DNS also returned Cloudflare's three Email Routing MX hosts and its SPF policy. A DS record was not yet visible during the same session because DNSSEC activation was still propagating.

## Approved production target

The approved first hosted topology is the repository-owned Render Blueprint in `render.yaml`: one
public web service, one private API service, and managed PostgreSQL in Render's Ohio region. The
Cloudflare redirect remains the active public behavior until the Render hostname passes API, web,
cookie, navigation, and responsive smoke checks. The cutover then removes the redirect and replaces
only the apex and `www` web records with Render's displayed DNS targets. Email Routing records,
DNSSEC, registrar protection, and the support forwarding route remain unchanged.

## Change procedure

During cutover, use the procedure in `docs/operations/render-production.md`. Keep HTTPS, DNSSEC,
WHOIS redaction, auto-renew, and inbound support routing enabled. Re-run public HTTP, TLS, MX, SPF,
and DS checks after the domain change, and update this record without committing account or contact
details.
