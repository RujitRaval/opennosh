# Developer starters and trial gate

The JavaScript and Python starters demonstrate one deliberately small integration: search for a
public food, fetch its public detail, require verified or stale verified release proof, and display
the source, license, attribution, release, and direct provenance path.

## Run a starter

For JavaScript:

```sh
cd examples/javascript-public-read
npm install
npm start
```

For Python:

```sh
cd examples/python-public-read
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

Both use `hosted` by default. Set `OPENNOSH_TARGET` to an origin-only HTTPS self-hosted endpoint, or
an exact loopback HTTP origin during local development. `OPENNOSH_QUERY` changes the demonstration
search term. Neither setting may contain credentials.

## Package evidence

`make package-check` builds the Python wheel and npm tarball, installs each artifact into an empty
temporary environment, verifies imports resolve inside that environment, and runs both starters
against the same bounded hosted/self-hosted response contract. The fixture rejects bearer headers
and cookies. This is the release gate; running examples from the source checkout is not package
evidence.

## External trial reports

Accepted reports live in `docs/evidence/developer-trials/` and validate against
`schemas/developer-integration-trial.schema.json`. A report records public GitHub operator and
reviewer logins, client versions, immutable artifact hashes, the endpoint kind, operation names,
boolean results, and redacted problem codes. It contains no endpoint URL, token, query, food payload,
IP address, or credential.

The operator must not be a repository collaborator or a commit author in the preceding 90 days.
The reviewer must be a different active maintainer. Every operator login is unique across accepted
reports. The compatibility manifest cannot change from `preview` to `stable` until two distinct
operators pass the gate; today there are zero accepted reports, so no external-adoption claim is
made.

## Rollback

Starter examples and evidence validation do not activate production behavior. Roll back a broken
artifact by deprecating its immutable package version and publishing a reviewed patch; never replace
an existing registry artifact. Leave MCP and embed discovery disabled, retain the last valid trial
reports, and keep the compatibility manifest in preview until a separately reviewed stability
change passes the two-operator gate.
