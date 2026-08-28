from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from opennosh_api.first_contribution.contracts import (
    FirstContributionPackage,
    canonical_json,
)
from opennosh_api.first_contribution.prepare import (
    FirstContributionPreparationError,
    load_first_contribution_package,
    prepare_usda_first_contribution,
)
from opennosh_api.foodpacks.loader import prepare_food_pack
from opennosh_api.governance.contracts import ApprovedChangeSet, ApprovedFileChange


def usda_source() -> dict[str, object]:
    return {
        "fdcId": 1105314,
        "dataType": "Foundation",
        "description": "Bananas, ripe and slightly ripe, raw",
        "publicationDate": "2020-04-01",
        "foodNutrients": [
            {"nutrient": {"id": 1008}, "amount": 97},
            {"nutrient": {"id": 1003}, "amount": 0.74},
            {"nutrient": {"id": 1004}, "amount": 0.29},
            {"nutrient": {"id": 1005}, "amount": 23.0},
        ],
    }


def write_source(path: Path, source: dict[str, object] | None = None) -> None:
    path.write_text(json.dumps(source or usda_source()), encoding="utf-8")


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
    material["package_digest"] = hashlib.sha256(
        canonical_json({key: value for key, value in material.items() if key != "package_digest"})
    ).hexdigest()
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
