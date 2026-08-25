# Domain operations

This record captures the public, non-secret configuration for `opennosh.org`. It deliberately excludes Cloudflare account identifiers, registrant contact details, payment information, nameserver assignments, and the private forwarding destination.

## Current purpose

`opennosh.org` is the stable public address for the hosted public Commons and private Tracker.
Render serves the apex domain, and `www.opennosh.org` permanently redirects to the apex.

## Cloudflare configuration

Verified after the production cutover on 2026-08-25:

- Registrar status is active and auto-renew is enabled.
- WHOIS contact-data redaction is enabled.
- The transfer lock is active under the new-registration hold.
- DNSSEC is enabled and the public DS record is present.
- The apex is a DNS-only CNAME to `opennosh-web.onrender.com`.
- `www` is a DNS-only A record to Render's documented `216.24.57.1` load-balancer address. A
  direct DNS-only CNAME to the service hostname produced Cloudflare error 1000 during cutover; the
  documented Render address passed direct TLS and hostname-routing checks before it was saved.
- The previous 301 redirect to GitHub is disabled, not deleted, so rollback remains available.
- Cloudflare Email Routing is enabled with its required MX and SPF records.
- `support@opennosh.org` forwards inbound mail to a verified private destination.
- The catch-all email rule remains disabled, so misspelled and unplanned addresses are not silently accepted.

Cloudflare Email Routing provides inbound forwarding only. opennosh does not currently pay for a hosted mailbox or custom-domain outbound SMTP service, and replies may therefore come from the project owner's normal mailbox.

## Public verification

The following public behavior was verified after DNS propagation:

```text
http://opennosh.org                         -> 301 https://opennosh.org/
https://opennosh.org                        -> 307 /en
https://www.opennosh.org                    -> 301 https://opennosh.org/
https://opennosh.org/api/v1/healthz         -> 200, database connected
```

The public homepage, Tracker, Explore, and Contribute routes returned `200`. The logo and four
route-scoped WOFF2 font assets returned `200` with correct content types, the browser console was
clean, and mobile, tablet, and desktop smoke renders passed. Public DNS continued to return all
three Cloudflare Email Routing MX hosts, the SPF policy, and the DNSSEC DS record.

## Approved production target

The active hosted topology is the repository-owned Render Blueprint in `render.yaml`: one public
web service, one private API service, and managed PostgreSQL in Render's Ohio region. Render deploys
from `main` only after GitHub checks pass. Email Routing records, DNSSEC, registrar protection, and
the support forwarding route remain independent from the website records.

## Change procedure

For future changes, use the procedure in `docs/operations/render-production.md`. Keep HTTPS,
DNSSEC, WHOIS redaction, auto-renew, and inbound support routing enabled. Re-run public HTTP, TLS,
MX, SPF, and DS checks after any domain change, and update this record without committing account
or contact details.
