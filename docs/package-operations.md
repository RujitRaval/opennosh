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

## Publication controls

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

## Initial publication checklist

- [ ] Merge the package release pull request after all required checks pass.
- [ ] Refresh local `main` and rebuild both artifacts from the merge commit.
- [ ] Stage and approve the first npm release with the project owner's two-factor authentication.
- [ ] Configure npm trusted publishing for the new `opennosh` project.
- [ ] Configure the PyPI pending trusted publisher.
- [ ] Run `publish packages` manually from `main`.
- [ ] Verify both public registry pages, owners, versions, hashes, license files, source links, and
      install commands.
- [ ] Update this record, the launch plan, product decisions, TODO ledger, and README with the exact
      public evidence through a follow-up branch and pull request.

Do not claim either name is reserved before its public registry page resolves successfully.
