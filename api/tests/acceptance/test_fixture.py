from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from opennosh_api.acceptance.adapters import acceptance_publication_adapter_registry
from opennosh_api.acceptance.entrypoint import main
from opennosh_api.acceptance.fixtures import (
    ACCEPTANCE_RELEASE_VERSION,
    ACCEPTANCE_SOURCE,
    ACCEPTANCE_SOURCE_ID,
    hand_fixture_to_runtime,
    materialize_browser_acceptance_fixture,
)
from opennosh_api.nonproduction_keys import (
    ACCEPTANCE_MANIFEST_KEY_ID,
    ACCEPTANCE_MANIFEST_VERIFYING_KEY,
    ACCEPTANCE_RECEIPT_KEY_ID,
    ACCEPTANCE_RECEIPT_VERIFYING_KEY,
)
from opennosh_api.public.artifacts import LocalArtifactStore, PublicArtifactReadService
from opennosh_api.public_commons.manifests import ManifestKeyRing
from opennosh_api.publication.receipts import PublicationReceiptKeyRing

PUBLISHED_AT = datetime(2026, 8, 26, 16, tzinfo=UTC)


@pytest.mark.asyncio
async def test_fixture_materializes_one_receipt_bound_release(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    state = tmp_path / "state"

    metadata = await materialize_browser_acceptance_fixture(
        artifacts,
        state,
        published_at=PUBLISHED_AT,
    )
    repeated = await materialize_browser_acceptance_fixture(artifacts, state)

    assert repeated == metadata
    assert metadata.release_version == ACCEPTANCE_RELEASE_VERSION
    assert metadata.published_at == PUBLISHED_AT
    assert (state / "published-at.txt").read_text() == "2026-08-26T16:00:00Z"
    with pytest.raises(ValueError, match="conflicts with existing state"):
        await materialize_browser_acceptance_fixture(
            artifacts,
            state,
            published_at=datetime(2026, 8, 26, 17, tzinfo=UTC),
        )
    assert (
        metadata.receipt_digest
        == hashlib.sha256((artifacts / metadata.receipt_object_key).read_bytes()).hexdigest()
    )
    assert json.loads((state / "fixture.json").read_bytes()) == metadata.model_dump(mode="json")

    service = PublicArtifactReadService(
        store=LocalArtifactStore(artifacts),
        manifest_keys=ManifestKeyRing.from_config(
            f"{ACCEPTANCE_MANIFEST_KEY_ID}:{ACCEPTANCE_MANIFEST_VERIFYING_KEY}"
        ),
        receipt_keys=PublicationReceiptKeyRing.from_json(
            json.dumps({ACCEPTANCE_RECEIPT_KEY_ID: ACCEPTANCE_RECEIPT_VERIFYING_KEY})
        ),
        checkpoint_path=state / "checkpoint.json",
    )
    try:
        food = await service.food(ACCEPTANCE_SOURCE, ACCEPTANCE_SOURCE_ID, now=PUBLISHED_AT)
        exact = await service.food(
            ACCEPTANCE_SOURCE,
            ACCEPTANCE_SOURCE_ID,
            release_version=ACCEPTANCE_RELEASE_VERSION,
        )
        provenance, release = await service.provenance(
            ACCEPTANCE_SOURCE,
            ACCEPTANCE_SOURCE_ID,
            release_version=ACCEPTANCE_RELEASE_VERSION,
        )
    finally:
        await service.aclose()

    assert food == exact
    assert food.record.name == "Rajma masala"
    assert food.release.state == "verified"
    assert release.manifest.publication_receipt_key == metadata.receipt_object_key
    assert b"Verified evidence" in provenance


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("persisted", "message"),
    [
        ("not-a-timestamp", "publication time is invalid"),
        ("2026-08-26T16:00:00", "must include a timezone"),
    ],
)
async def test_fixture_rejects_invalid_persisted_publication_time(
    tmp_path: Path,
    persisted: str,
    message: str,
) -> None:
    artifacts = tmp_path / persisted.replace(":", "-") / "artifacts"
    state = artifacts.parent / "state"
    state.mkdir(parents=True)
    (state / "published-at.txt").write_text(persisted)

    with pytest.raises(ValueError, match=message):
        await materialize_browser_acceptance_fixture(artifacts, state)


def test_fixture_command_refuses_production(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.setenv("OPENNOSH_ACCEPTANCE_FIXTURES", "1")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "opennosh-acceptance-fixture",
            "--artifact-directory",
            str(tmp_path / "artifacts"),
            "--state-directory",
            str(tmp_path / "state"),
        ],
    )

    with pytest.raises(SystemExit, match="explicit development/test environment and opt-in"):
        main()


def test_fixture_command_refuses_unset_environment_and_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)
    monkeypatch.delenv("OPENNOSH_ACCEPTANCE_FIXTURES", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "opennosh-acceptance-fixture",
            "--artifact-directory",
            str(tmp_path / "artifacts"),
            "--state-directory",
            str(tmp_path / "state"),
        ],
    )

    with pytest.raises(SystemExit, match="explicit development/test environment and opt-in"):
        main()


def test_acceptance_adapter_registry_refuses_production(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.setenv("OPENNOSH_ACCEPTANCE_FIXTURES", "1")

    with pytest.raises(RuntimeError, match="development/test environment and explicit opt-in"):
        acceptance_publication_adapter_registry(
            state_root=tmp_path / "state",
            artifact_root=tmp_path / "artifacts",
            clock=lambda: PUBLISHED_AT,
        )


def test_fixture_handoff_is_a_noop_outside_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 10001, raising=False)
    monkeypatch.setattr(
        os,
        "chown",
        lambda *_args, **_kwargs: pytest.fail("non-root handoff must not chown"),
        raising=False,
    )

    hand_fixture_to_runtime(tmp_path / "artifacts", tmp_path / "state", uid=1, gid=1)


def test_fixture_handoff_covers_artifacts_and_writable_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    state = tmp_path / "state"
    pointer = artifacts / "latest" / "v1.json"
    metadata = state / "fixture.json"
    pointer.parent.mkdir(parents=True)
    state.mkdir()
    pointer.write_text("signed pointer")
    metadata.write_text("{}")
    ownership: list[tuple[Path, int, int, bool]] = []

    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(
        os,
        "chown",
        lambda path, uid, gid, *, follow_symlinks: ownership.append(
            (Path(path), uid, gid, follow_symlinks)
        ),
        raising=False,
    )

    hand_fixture_to_runtime(artifacts, state, uid=10001, gid=10001)

    assert set(ownership) == {
        (artifacts, 10001, 10001, False),
        (artifacts / "latest", 10001, 10001, False),
        (pointer, 10001, 10001, False),
        (state, 10001, 10001, False),
        (metadata, 10001, 10001, False),
    }
