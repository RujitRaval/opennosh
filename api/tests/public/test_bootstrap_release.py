from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.foodpacks.validation import validate_pack_directories
from opennosh_api.public.artifacts import PublicReadReleaseManifest
from opennosh_api.public.bootstrap import (
    StarterReleaseInventory,
    build_starter_release,
    inventory_sha256,
    load_inventory,
    load_verified_inventory,
    verify_starter_release,
)
from opennosh_api.public_commons.manifests import SignedEnvelope

ROOT = Path(__file__).resolve().parents[3]
PACKS = ROOT / "packs"
PUBLISHED_AT = datetime(2026, 8, 27, 2, tzinfo=UTC)
SOURCE_COMMIT = "a" * 40
DECISION = "https://github.com/RujitRaval/opennosh/issues/97"
FOUNDATIONAL_PACKS = (
    "common-vegetarian-proteins",
    "gujarati-home-cooking",
    "indian-staples-north",
    "supplements-and-powders",
)


def _write_private_key(path: Path) -> str:
    private_key = Ed25519PrivateKey.generate()
    encoded = (
        base64.urlsafe_b64encode(
            private_key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
        )
        .decode()
        .rstrip("=")
    )
    path.write_text(encoded, encoding="ascii")
    os.chmod(path, 0o600)
    return encoded


def _build(
    output: Path,
    manifest_key: Path,
    receipt_key: Path,
    *,
    packs_root: Path = PACKS,
) -> StarterReleaseInventory:
    return build_starter_release(
        packs_root=packs_root,
        output_directory=output,
        release_version="0.56.0.0",
        published_at=PUBLISHED_AT,
        source_commit=SOURCE_COMMIT,
        manifest_key_id="starter-manifest-2026-01",
        manifest_private_key_path=manifest_key,
        receipt_key_id="starter-receipt-2026-01",
        receipt_private_key_path=receipt_key,
        decision_reference=DECISION,
        approving_actor="github:RujitRaval",
    )


def _expected_release_counts(packs_root: Path = PACKS) -> tuple[int, int]:
    manifests = [
        yaml.safe_load((path / "pack.yaml").read_text(encoding="utf-8"))
        for path in sorted(packs_root.iterdir())
        if path.is_dir() and (path / "pack.yaml").is_file()
    ]
    return len(manifests), sum(int(manifest["entry_count"]) for manifest in manifests)


def _catalog_with_extension(tmp_path: Path) -> Path:
    catalog = tmp_path / "fixture-repository" / "packs"
    shutil.copytree(PACKS, catalog)
    extension = catalog / "community-extension"
    foods = extension / "foods"
    foods.mkdir(parents=True)
    (extension / "pack.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "community-extension",
                "name": "Community extension",
                "description": "A governed extension used to prove that the catalog is additive.",
                "version": "1.0.0",
                "locale": "en",
                "license": "CC0-1.0",
                "maintainers": [{"github": "RujitRaval"}],
                "entry_count": 1,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (foods / "foods.yaml").write_text(
        yaml.safe_dump(
            [
                {
                    "slug": "community-extension-recipe-fixture",
                    "name": "Governed extension recipe fixture",
                    "category": "mixed-dish",
                    "tags": ["test-fixture"],
                    "contributed_by": "OpenNosh-test-suite",
                    "provenance": "published_recipe_calculation",
                    "source_uri": None,
                    "source_license": "contributor-original",
                    "source_note": (
                        "Synthetic contributor-authored record used only by this isolated test. "
                        "Batch formula and Cooked yield are intentionally fixture-only; actual "
                        "recipes vary."
                    ),
                    "basis": "per_100g",
                    "nutrients": {
                        "energy_kcal": 333,
                        "protein_g": 17.0,
                        "fat_g": 13.0,
                        "carbohydrate_g": 37.0,
                    },
                    "portions": [{"name": "100 g", "grams": 100}],
                }
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return catalog


@pytest.mark.asyncio
async def test_starter_release_is_complete_deterministic_and_verifiable(tmp_path: Path) -> None:
    manifest_key = tmp_path / "manifest.key"
    receipt_key = tmp_path / "receipt.key"
    manifest_secret = _write_private_key(manifest_key)
    receipt_secret = _write_private_key(receipt_key)

    first = _build(tmp_path / "first", manifest_key, receipt_key)
    second = _build(tmp_path / "second", manifest_key, receipt_key)
    expected_pack_count, expected_food_count = _expected_release_counts()

    assert first == second
    assert first.release_version == "0.56.0.0"
    assert first.food_count == expected_food_count
    assert first.pack_count == expected_pack_count
    assert len(first.objects) == first.food_count * 2 + first.pack_count + 3
    assert first.objects[-1].object_key == "latest/v1.json"
    assert first.objects[-1].mutable_pointer is True
    assert all(not item.mutable_pointer for item in first.objects[:-1])
    first_inventory_path = tmp_path / "first" / "inventory.json"
    second_inventory_path = tmp_path / "second" / "inventory.json"
    expected_inventory_sha256 = inventory_sha256(first_inventory_path)
    assert expected_inventory_sha256 == inventory_sha256(second_inventory_path)
    assert load_inventory(first_inventory_path) == first
    assert (
        load_verified_inventory(
            first_inventory_path,
            expected_sha256=expected_inventory_sha256,
        )
        == first
    )
    with pytest.raises(ValueError, match="operator trust anchor"):
        load_verified_inventory(first_inventory_path, expected_sha256="0" * 64)

    for item in first.objects:
        first_payload = (tmp_path / "first" / item.object_key).read_bytes()
        second_payload = (tmp_path / "second" / item.object_key).read_bytes()
        assert first_payload == second_payload
        assert hashlib.sha256(first_payload).hexdigest() == item.digest
        assert len(first_payload) == item.size_bytes

    public_payload = b"".join(
        (tmp_path / "first" / item.object_key).read_bytes() for item in first.objects
    )
    assert manifest_secret.encode() not in public_payload
    assert receipt_secret.encode() not in public_payload
    await verify_starter_release(tmp_path / "first", first)


@pytest.mark.asyncio
async def test_starter_release_adds_a_valid_governed_pack(tmp_path: Path) -> None:
    catalog = _catalog_with_extension(tmp_path)
    pack_directories = tuple(
        path
        for path in sorted(catalog.iterdir())
        if path.is_dir() and (path / "pack.yaml").is_file()
    )
    report = validate_pack_directories(pack_directories)
    assert report.valid
    assert report.errors == ()
    assert report.warnings == ()

    manifest_key = tmp_path / "manifest.key"
    receipt_key = tmp_path / "receipt.key"
    _write_private_key(manifest_key)
    _write_private_key(receipt_key)
    output = tmp_path / "release"
    inventory = _build(
        output,
        manifest_key,
        receipt_key,
        packs_root=catalog,
    )

    catalog_pack_count, catalog_food_count = _expected_release_counts()
    foundational_food_count = sum(
        int(
            yaml.safe_load((PACKS / pack_id / "pack.yaml").read_text(encoding="utf-8"))[
                "entry_count"
            ]
        )
        for pack_id in FOUNDATIONAL_PACKS
    )
    assert foundational_food_count == 165
    assert inventory.pack_count == catalog_pack_count + 1
    assert inventory.food_count == catalog_food_count + 1

    manifest_path = output / "releases" / "v1" / "release-0.56.0.0.json"
    envelope = SignedEnvelope.model_validate_json(manifest_path.read_bytes())
    manifest = PublicReadReleaseManifest.model_validate(envelope.payload)
    assert "community-extension" in {pack.pack_id for pack in manifest.packs}
    assert "community-extension-recipe-fixture" in {
        food.source_id for food in manifest.foods
    }
    await verify_starter_release(output, inventory)


def test_starter_release_refuses_private_keys_with_broad_permissions(tmp_path: Path) -> None:
    manifest_key = tmp_path / "manifest.key"
    receipt_key = tmp_path / "receipt.key"
    _write_private_key(manifest_key)
    _write_private_key(receipt_key)
    os.chmod(manifest_key, 0o644)

    with pytest.raises(PermissionError, match="group or others"):
        _build(tmp_path / "release", manifest_key, receipt_key)


def test_starter_release_refuses_reserved_nonproduction_key_ids(tmp_path: Path) -> None:
    manifest_key = tmp_path / "manifest.key"
    receipt_key = tmp_path / "receipt.key"
    _write_private_key(manifest_key)
    _write_private_key(receipt_key)

    with pytest.raises(ValueError, match="invalid or reserved"):
        build_starter_release(
            packs_root=PACKS,
            output_directory=tmp_path / "release",
            release_version="0.56.0.0",
            published_at=PUBLISHED_AT,
            source_commit=SOURCE_COMMIT,
            manifest_key_id="development",
            manifest_private_key_path=manifest_key,
            receipt_key_id="starter-receipt-2026-01",
            receipt_private_key_path=receipt_key,
            decision_reference=DECISION,
            approving_actor="github:RujitRaval",
        )


def test_inventory_contains_only_public_verification_material(tmp_path: Path) -> None:
    manifest_key = tmp_path / "manifest.key"
    receipt_key = tmp_path / "receipt.key"
    _write_private_key(manifest_key)
    _write_private_key(receipt_key)
    inventory = _build(tmp_path / "release", manifest_key, receipt_key)

    encoded = json.dumps(inventory.model_dump(mode="json"))
    assert "private" not in encoded.casefold()
    assert len(inventory.manifest_verifying_key) == 43
    assert len(inventory.receipt_verifying_key) == 43
