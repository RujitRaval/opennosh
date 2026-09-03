# Developer integration trial evidence

No external trial report has been accepted yet. The developer kit therefore remains preview
software and no adoption or general-availability claim is made.

Accepted reports are immutable JSON files validated by
`schemas/developer-integration-trial.schema.json`. Each report records only public GitHub identities,
artifact versions and hashes, an endpoint kind, operation names, boolean assertions, redacted
problem codes, and UTC review times. Reports must never include endpoint URLs, tokens, queries, food
payloads, IP addresses, credentials, or other private data.

An operator is independent only when they are not a repository collaborator and authored no commit
in the preceding 90 days. One active maintainer reviews each report in its own pull request. Two
different operator logins are required before the compatibility manifest may move from `preview` to
`stable`; the repository gate enforces that threshold.
