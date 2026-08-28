from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.first_contribution.contracts import (
    FIRST_SOURCE_URI,
    FirstContributionPackage,
    FirstContributionReceipt,
    derived_id,
    package_material_digest,
)
from opennosh_api.first_contribution.prepare import (
    FirstContributionPreparationError,
    _build_package,
    load_first_contribution_package,
    prepare_usda_first_contribution,
)
from opennosh_api.foodpacks.loader import prepare_food_pack
from opennosh_api.governance.contracts import ApprovedChangeSet, ApprovedFileChange
from opennosh_api.public.bootstrap import build_starter_release, verify_starter_release


def usda_source() -> dict[str, object]:
    return {
        "fdcId": 1105314,
        "dataType": "Foundation",
        "description": "Bananas, ripe and slightly ripe, raw",
        "publicationDate": "4/1/2020",
        "foodNutrients": [
            {"nutrient": {"id": 1008}, "amount": 97},
            {"nutrient": {"id": 1003}, "amount": 0.74},
            {"nutrient": {"id": 1004}, "amount": 0.29},
            {"nutrient": {"id": 1005}, "amount": 23.0},
        ],
    }


def write_source(path: Path, source: dict[str, object] | None = None) -> None:
    path.write_text(json.dumps(source or usda_source()), encoding="utf-8")


def write_private_key(path: Path) -> None:
    encoded = base64.urlsafe_b64encode(
        Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    ).decode().rstrip("=")
    path.write_text(encoded, encoding="ascii")
    os.chmod(path, 0o600)


def test_prepare_is_deterministic_and_reuses_identical_output(tmp_path: Path) -> None:
    source = tmp_path / "usda.json"
    package_path = tmp_path / "package.json"
    write_source(source)

    first = prepare_usda_first_contribution(source, package_path)
    original = package_path.read_bytes()
    second = prepare_usda_first_contribution(source, package_path)

    assert first == second
    assert original == package_path.read_bytes() == first.canonical_bytes()
    assert package_path.stat().st_mode & 0o777 == 0o600
    assert first.draft_fields["evidence_type"] == "public_document"
    assert first.evidence_manifest["rights_state"] == "reference_only"
    assert first.approved_changes["pack_id"] == "common-fruits"
    package_path.chmod(0o644)
    with pytest.raises(FirstContributionPreparationError, match="mode-0600"):
        prepare_usda_first_contribution(source, package_path)


def test_prepare_accepts_the_prior_iso_usda_publication_date_encoding(
    tmp_path: Path,
) -> None:
    source_value = usda_source()
    source_value["publicationDate"] = "2020-04-01"
    source = tmp_path / "usda.json"
    write_source(source, source_value)

    package = prepare_usda_first_contribution(source, tmp_path / "package.json")

    assert package.fdc_id == "1105314"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fdcId", 999),
        ("dataType", "Survey (FNDDS)"),
        ("description", "Banana"),
        ("publicationDate", "2020-04-02"),
    ],
)
def test_prepare_rejects_source_identity_drift(
    tmp_path: Path, field: str, value: object
) -> None:
    source_value = usda_source()
    source_value[field] = value
    source = tmp_path / "usda.json"
    write_source(source, source_value)

    with pytest.raises(FirstContributionPreparationError):
        prepare_usda_first_contribution(source, tmp_path / "package.json")


def test_prepare_rejects_nutrient_drift_and_symlinks(tmp_path: Path) -> None:
    source_value = usda_source()
    nutrients = source_value["foodNutrients"]
    assert isinstance(nutrients, list)
    nutrients[0]["amount"] = 98  # type: ignore[index]
    changed = tmp_path / "changed.json"
    write_source(changed, source_value)
    with pytest.raises(FirstContributionPreparationError, match="nutrient"):
        prepare_usda_first_contribution(changed, tmp_path / "package.json")

    valid = tmp_path / "valid.json"
    write_source(valid)
    link = tmp_path / "link.json"
    link.symlink_to(valid)
    with pytest.raises(FirstContributionPreparationError, match="non-symlink"):
        prepare_usda_first_contribution(link, tmp_path / "linked-package.json")


def test_prepare_rejects_duplicate_pinned_nutrient_malformed_and_oversized_input(
    tmp_path: Path,
) -> None:
    duplicate_value = usda_source()
    nutrients = duplicate_value["foodNutrients"]
    assert isinstance(nutrients, list)
    nutrients.append({"nutrient": {"id": 1008}, "amount": 97})
    duplicate = tmp_path / "duplicate.json"
    write_source(duplicate, duplicate_value)
    with pytest.raises(FirstContributionPreparationError, match="duplicate"):
        prepare_usda_first_contribution(duplicate, tmp_path / "duplicate-package.json")

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    with pytest.raises(FirstContributionPreparationError, match="valid JSON"):
        prepare_usda_first_contribution(malformed, tmp_path / "malformed-package.json")

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b" " * (2 * 1024 * 1024) + b"}")
    with pytest.raises(FirstContributionPreparationError, match="between 1 and"):
        prepare_usda_first_contribution(oversized, tmp_path / "oversized-package.json")


def test_prepare_refuses_conflicting_output_and_tampered_package(tmp_path: Path) -> None:
    source = tmp_path / "usda.json"
    output = tmp_path / "package.json"
    write_source(source)
    package = prepare_usda_first_contribution(source, output)
    output.write_text("{}", encoding="utf-8")
    with pytest.raises(FirstContributionPreparationError, match="different bytes"):
        prepare_usda_first_contribution(source, output)

    material = package.model_dump(mode="json")
    material["record_id"] = "different"
    with pytest.raises(ValueError):
        FirstContributionPackage.model_validate(material)


@pytest.mark.parametrize("tamper", ["draft", "evidence", "changes"])
def test_commit_loader_rejects_self_consistent_noncanonical_package(
    tmp_path: Path,
    tamper: str,
) -> None:
    source = tmp_path / "usda.json"
    write_source(source)
    package = prepare_usda_first_contribution(source, tmp_path / "canonical.json")
    material = package.model_dump(mode="json")
    if tamper == "draft":
        material["draft_fields"]["energy_kcal"] = "98"
    elif tamper == "evidence":
        material["evidence_manifest"]["publisher"] = "Unreviewed publisher"
    else:
        changes = ApprovedChangeSet.from_json(material["approved_changes"])
        files = list(changes.files)
        files[0] = ApprovedFileChange(
            path=files[0].path,
            content=files[0].content + "Unreviewed content\n",
        )
        material["approved_changes"] = ApprovedChangeSet.build(
            pack_id=changes.pack_id,
            files=files,
        ).as_json()
    material["package_digest"] = package_material_digest(material)
    for field, purpose in (
        ("source_actor_id", "source-actor"),
        ("draft_id", "draft"),
        ("submission_id", "submission"),
        ("evidence_id", "evidence"),
        ("role_assignment_id", "steward-role"),
        ("decision_id", "decision"),
        ("publication_intent_id", "publication-intent"),
    ):
        material[field] = str(derived_id(material["package_digest"], purpose))
    material["evidence_manifest"]["evidence_id"] = material["evidence_id"]
    tampered = FirstContributionPackage.model_validate(material)
    path = tmp_path / f"tampered-{tamper}.json"
    path.write_bytes(tampered.canonical_bytes())

    with pytest.raises(FirstContributionPreparationError, match="pinned reviewed material"):
        load_first_contribution_package(path)


def test_generated_common_fruits_pack_is_releaseable(tmp_path: Path) -> None:
    source = tmp_path / "usda.json"
    write_source(source)
    package = prepare_usda_first_contribution(source, tmp_path / "package.json")
    changes = ApprovedChangeSet.from_json(package.approved_changes)
    for change in changes.files:
        destination = tmp_path / change.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(change.content, encoding="utf-8")

    prepared = prepare_food_pack(tmp_path / "packs/common-fruits")

    assert prepared.pack_id == "common-fruits"
    assert prepared.pack_version == "1.0.0"
    assert not prepared.pack_rejected
    assert not prepared.errors
    assert len(prepared.records) == 1
    assert prepared.records[0].slug == "bananas-ripe-and-slightly-ripe-raw"


@pytest.mark.asyncio
async def test_generated_pack_passes_canonical_release_builder(tmp_path: Path) -> None:
    source = tmp_path / "usda.json"
    write_source(source)
    package = prepare_usda_first_contribution(source, tmp_path / "package.json")
    changes = ApprovedChangeSet.from_json(package.approved_changes)
    for change in changes.files:
        destination = tmp_path / change.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(change.content, encoding="utf-8")
    key_root = tmp_path.parent / f"{tmp_path.name}-signing"
    key_root.mkdir()
    manifest_key = key_root / "manifest.key"
    receipt_key = key_root / "receipt.key"
    write_private_key(manifest_key)
    write_private_key(receipt_key)
    release_root = tmp_path.parent / f"{tmp_path.name}-release"

    inventory = build_starter_release(
        packs_root=tmp_path / "packs",
        output_directory=release_root,
        release_version="0.63.0.0",
        published_at=datetime(2026, 8, 28, 18, tzinfo=UTC),
        source_commit="a" * 40,
        manifest_key_id="first-contribution-manifest-2026-01",
        manifest_private_key_path=manifest_key,
        receipt_key_id="first-contribution-receipt-2026-01",
        receipt_private_key_path=receipt_key,
        decision_reference="https://github.com/RujitRaval/opennosh/issues/116",
        approving_actor="github:RujitRaval",
    )

    assert inventory.pack_count == 1
    assert inventory.food_count == 1
    await verify_starter_release(release_root, inventory)


@pytest.mark.parametrize(
    ("digest", "purpose"),
    [
        ("A" * 64, "draft"),
        ("a" * 64, ""),
        ("a" * 64, "dräft"),
    ],
)
def test_deterministic_identity_rejects_unbounded_inputs(digest: str, purpose: str) -> None:
    with pytest.raises(ValueError):
        derived_id(digest, purpose)


@pytest.mark.parametrize(
    "tamper", ["pack", "source", "files", "identity", "evidence_identity", "digest"]
)
def test_package_contract_rejects_each_trust_boundary(tamper: str) -> None:
    package = _build_package("a" * 64)
    material = package.model_dump(mode="json")
    if tamper == "pack":
        material["draft_fields"]["pack_id"] = "other-pack"
    elif tamper == "source":
        material["evidence_manifest"]["canonical_uri"] = FIRST_SOURCE_URI + "?changed=1"
    elif tamper == "files":
        material["approved_changes"]["files"] = material["approved_changes"]["files"][:2]
    elif tamper == "identity":
        material["draft_id"] = str(uuid4())
    elif tamper == "evidence_identity":
        material["evidence_manifest"]["evidence_id"] = str(uuid4())
    else:
        material["package_digest"] = "b" * 64

    with pytest.raises(ValueError):
        FirstContributionPackage.model_validate(material)


def test_receipt_requires_timezone_aware_decision_time() -> None:
    package = _build_package("a" * 64)

    with pytest.raises(ValueError, match="timezone"):
        FirstContributionReceipt(
            package_digest=package.package_digest,
            source_actor_id=package.source_actor_id,
            steward_actor_id=uuid4(),
            draft_id=package.draft_id,
            evidence_id=package.evidence_id,
            evidence_manifest_digest="b" * 64,
            decision_id=package.decision_id,
            publication_intent_id=package.publication_intent_id,
            approved_payload_digest=package.approved_changes["digest"],
            decided_at=datetime(2026, 8, 28, 17, 0),
        )


def test_loader_rejects_non_object_source_and_invalid_package_json(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text("[]", encoding="utf-8")
    with pytest.raises(FirstContributionPreparationError, match="JSON object"):
        prepare_usda_first_contribution(source, tmp_path / "package.json")

    package = tmp_path / "invalid-package.json"
    package.write_text("{}", encoding="utf-8")
    with pytest.raises(FirstContributionPreparationError, match="package is invalid"):
        load_first_contribution_package(package)


def test_prepare_rejects_missing_source_and_invalid_nutrient_amount(tmp_path: Path) -> None:
    with pytest.raises(FirstContributionPreparationError, match="inspected"):
        prepare_usda_first_contribution(tmp_path / "missing.json", tmp_path / "package.json")

    source_value = usda_source()
    nutrients = source_value["foodNutrients"]
    assert isinstance(nutrients, list)
    nutrients[0]["amount"] = "not-a-number"  # type: ignore[index]
    source = tmp_path / "invalid-amount.json"
    write_source(source, source_value)
    with pytest.raises(FirstContributionPreparationError, match="amount is invalid"):
        prepare_usda_first_contribution(source, tmp_path / "invalid-package.json")
