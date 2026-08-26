# Package operations

This document records the non-secret release design for the public `opennosh` names on PyPI and
npm. Registry account identifiers, recovery information, authentication factors, and credentials
must never be committed.

## Public artifacts

| Registry | Name | Purpose | Version form |
|---|---|---|---|
| PyPI | `opennosh` | Installable FastAPI application modules and the `opennosh` data-management CLI | Exact four-part repository `VERSION` |
| npm | `opennosh` | `npx opennosh init` bootstrapper for cloning a safe local checkout | First three components of repository `VERSION` |

The npm bootstrapper has an immediate function. It is not an empty placeholder: npm prohibits
packages that exist only to reserve a name. It refuses to overwrite an existing path, passes clone
arguments directly to Git without a shell, and does not install Docker, run services, change global
configuration, or collect telemetry.

Reproduce the artifact, identity, installed-wheel, and npm bootstrap checks locally before any
publication attempt:

```shell
make package-check
```

## Publication controls

Before either registry validation or publication starts,
`.github/workflows/release-confidence.yml` must pass its package/install, browser-role,
upgrade/rollback and receipt-reconstruction, and supported self-host checks. A failing confidence
job blocks both PyPI and npm publication.

`.github/workflows/publish-packages.yml` is the only long-term publishing path. It:

- accepts published releases or a manual run from `main`;
- requires the release tag to equal `v` plus the canonical `VERSION`;
- refuses release commits that are not contained in `main`;
- builds and inspects the Python wheel and source archive;
- tests and dry-runs the npm tarball;
- uses the `pypi` and `npm` GitHub environments;
- restricts both publishing environments to `main` and `v*` release tags;
- grants OIDC identity-token permission only to the two publishing jobs;
- skips a registry version that already exists, making a repeated run safe; and
- publishes without stored registry tokens after trusted publishers are configured.

PyPI supports a pending trusted publisher for a project that does not yet exist. The publisher must
name GitHub owner `RujitRaval`, repository `opennosh`, workflow `publish-packages.yml`, and
environment `pypi`. The pending publisher does not reserve the name; the first successful workflow
publication does.

npm does not provide the same pending-project flow. The first tested npm release must therefore be
staged by the authenticated project owner and approved with two-factor authentication. After that
release creates the project, configure its GitHub Actions trusted publisher for owner
`RujitRaval`, repository `opennosh`, workflow `publish-packages.yml`, environment `npm`, and the
`npm publish` action. Future releases then use short-lived OIDC credentials and automatic
provenance.

## Verified initial release

The project owner approved the initial npm publication with two-factor authentication on
2026-08-21. The [publish packages workflow](https://github.com/RujitRaval/opennosh/actions/runs/32509681049)
then verified merged `main` commit `c7abc5eac93f6ac63b50a4f56d24ba5806f6f31d`, safely skipped the
existing npm version, and published the PyPI artifacts through OIDC. Both registries now have
active trusted publishers for repository
`RujitRaval/opennosh`, workflow `publish-packages.yml`, and their corresponding `npm` or `pypi`
GitHub environment.

| Registry | Public release | Integrity evidence | Verified command |
|---|---|---|---|
| PyPI | [`opennosh 0.22.0.0`](https://pypi.org/project/opennosh/0.22.0.0/) | Wheel SHA-256 `0ea7951dbc4e3d73623ee30bcf12ec3fe5af65044aea31e73aada207ea676493`; source archive SHA-256 `10472da52c111809491b1c286ad6e63ce54c4faea368644dceecb5ded613c814` | `uvx --from opennosh==0.22.0.0 opennosh --help` |
| npm | [`opennosh 0.22.0`](https://www.npmjs.com/package/opennosh) | SHA-512 `zh/gfwAILdomRGVShzoWbHBK9xvla96MQ0q9E5FHUI7mlHkc5scnCYXnn1+XmbMq7cjgNQY2NM4SjeRzMeQLJg==` | `npx --yes opennosh@0.22.0 --version` |

The public metadata identifies the canonical repository and `https://opennosh.org`, declares the
MIT software license, and includes the required license and notice files. The PyPI wheel and source
archive hashes match the public simple index; the npm tarball contains only `LICENSE`, `README.md`,
`bin/opennosh.mjs`, and `package.json`. Clean-cache executions of both verified commands succeeded.

## Initial publication checklist

- [x] Merge the package release pull request after all required checks pass.
- [x] Refresh local `main` and rebuild both artifacts from the merge commit.
- [x] Stage and approve the first npm release with the project owner's two-factor authentication.
- [x] Configure npm trusted publishing for the new `opennosh` project.
- [x] Configure the PyPI pending trusted publisher; the successful first release converted it to
      an active publisher.
- [x] Run `publish packages` manually from `main`.
- [x] Verify both public registry pages, owners, versions, hashes, license files, source links, and
      install commands.
- [x] Update this record, the launch plan, product decisions, TODO ledger, and README with the exact
      public evidence through a follow-up branch and pull request.

Both canonical names became reserved through successful public releases on 2026-08-21. Future
releases must use the trusted-publisher workflow and preserve the verification evidence above.
