from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from opennosh_api.importers.usda_branded_assessment import (
    BrandedAssessmentManifest,
    assess_archive,
    canonical_gtin,
    verify_archive,
)


def _archive(tmp_path: Path, rows: list[dict[str, object]]) -> tuple[Path, dict[str, object]]:
    path = tmp_path / "branded.zip"
    member = "branded.json"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(member, json.dumps({"BrandedFoods": rows}))
    with ZipFile(path) as archive:
        expanded = archive.getinfo(member).file_size
    manifest: dict[str, object] = {
        "schema_version": "1.0",
        "dataset_id": "branded-fixture",
        "filename": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
        "json_member": member,
        "uncompressed_size_bytes": expanded,
        "expected_rows": len(rows),
        "maximum_rows": 20,
        "maximum_activation_records": 2,
    }
    return path, manifest


def _row(
    fdc_id: int,
    gtin: str | None,
    *,
    description: str = "Example cereal",
    country: str | None = "United States",
    energy: int = 100,
) -> dict[str, object]:
    return {
        "fdcId": fdc_id,
        "dataType": "Branded",
        "description": description,
        "brandOwner": "Example Foods",
        "brandName": "Example",
        "gtinUpc": gtin,
        "marketCountry": country,
        "foodNutrients": [{"nutrient": {"id": 1008}, "amount": energy}],
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("036000291452", "00036000291452"),
        ("96385074", "00000096385074"),
        ("036000291453", None),
        ("not-a-gtin", None),
        (None, None),
    ],
)
def test_canonical_gtin_requires_supported_length_and_valid_check_digit(
    raw: str | None, expected: str | None
) -> None:
    assert canonical_gtin(raw) == expected


def test_assessment_quarantines_invalid_and_conflicting_identity_groups(
    tmp_path: Path,
) -> None:
    rows = [
        _row(1, "036000291452"),
        _row(2, "036000291452"),
        _row(3, "036000291452", description="Different cereal"),
        _row(4, "96385074", description="Unique oats"),
        _row(5, None),
        _row(6, "036000291453"),
    ]
    archive, payload = _archive(tmp_path, rows)
    manifest = BrandedAssessmentManifest.model_validate(payload)

    report = assess_archive(archive, manifest, workspace=tmp_path)

    assert report["rows_seen"] == 6
    assert report["valid_identity_rows"] == 4
    assert report["duplicate_identity_groups"] == 1
    assert report["duplicate_identity_records"] == 3
    assert report["conflicting_identity_groups"] == 1
    assert report["eligible_records_after_quarantine"] == 1
    assert report["quarantined_records"] == 5
    assert report["activation_status"] == "hold"
    assert report["activation_blockers"] == ["conflicting_gtin_country_identities"]


def test_archive_verification_rejects_tampering(tmp_path: Path) -> None:
    archive, payload = _archive(tmp_path, [_row(1, "036000291452")])
    manifest = BrandedAssessmentManifest.model_validate(payload)
    archive.write_bytes(archive.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="size"):
        verify_archive(archive, manifest)


def test_assessment_treats_nutrition_changes_as_identity_conflicts(tmp_path: Path) -> None:
    archive, payload = _archive(
        tmp_path,
        [_row(1, "036000291452"), _row(2, "036000291452", energy=120)],
    )
    manifest = BrandedAssessmentManifest.model_validate(payload)

    report = assess_archive(archive, manifest, workspace=tmp_path)

    assert report["duplicate_identity_groups"] == 1
    assert report["conflicting_identity_groups"] == 1
    assert report["exact_duplicate_identity_groups"] == 0


def test_assessment_rejects_pinned_row_count_drift(tmp_path: Path) -> None:
    archive, payload = _archive(tmp_path, [_row(1, "036000291452")])
    payload["expected_rows"] = 2
    manifest = BrandedAssessmentManifest.model_validate(payload)

    with pytest.raises(ValueError, match="row count"):
        assess_archive(archive, manifest, workspace=tmp_path)


def test_assessment_quarantines_duplicate_fdc_id_rows(tmp_path: Path) -> None:
    archive, payload = _archive(
        tmp_path,
        [_row(1, "036000291452"), _row(1, "96385074", description="Other product")],
    )
    manifest = BrandedAssessmentManifest.model_validate(payload)

    report = assess_archive(archive, manifest, workspace=tmp_path)

    assert report["rows_seen"] == 2
    assert report["duplicate_fdc_ids"] == 1
    assert report["eligible_records_after_quarantine"] == 1
    assert report["quarantined_records"] == 1
    assert report["activation_status"] == "hold"
    assert report["activation_blockers"] == ["duplicate_fdc_ids"]
