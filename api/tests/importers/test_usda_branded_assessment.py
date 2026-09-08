from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import opennosh_api.importers.usda_branded_assessment as assessment_module
import pytest
from opennosh_api.importers.usda_branded_assessment import (
    BrandedAssessmentManifest,
    assess_archive,
    canonical_gtin,
    load_manifest,
    main,
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


def test_archive_verification_rejects_same_size_checksum_tampering(tmp_path: Path) -> None:
    archive, payload = _archive(tmp_path, [_row(1, "036000291452")])
    manifest = BrandedAssessmentManifest.model_validate(payload)
    encoded = bytearray(archive.read_bytes())
    encoded[0] ^= 1
    archive.write_bytes(encoded)

    with pytest.raises(ValueError, match="SHA-256"):
        verify_archive(archive, manifest)


def test_archive_verification_rejects_filename_and_malformed_zip(tmp_path: Path) -> None:
    archive, payload = _archive(tmp_path, [_row(1, "036000291452")])
    wrong_name = BrandedAssessmentManifest.model_validate({**payload, "filename": "other.zip"})
    with pytest.raises(ValueError, match="filename"):
        verify_archive(archive, wrong_name)

    malformed = tmp_path / "malformed.zip"
    malformed.write_bytes(b"not a zip")
    malformed_manifest = BrandedAssessmentManifest.model_validate(
        {
            **payload,
            "filename": malformed.name,
            "sha256": hashlib.sha256(malformed.read_bytes()).hexdigest(),
            "size_bytes": malformed.stat().st_size,
        }
    )
    with pytest.raises(ValueError, match="valid ZIP"):
        verify_archive(malformed, malformed_manifest)


def test_archive_verification_rejects_member_and_expanded_size_drift(tmp_path: Path) -> None:
    archive, payload = _archive(tmp_path, [_row(1, "036000291452")])

    wrong_member = BrandedAssessmentManifest.model_validate(
        {**payload, "json_member": "other.json"}
    )
    with pytest.raises(ValueError, match="JSON member"):
        verify_archive(archive, wrong_member)

    wrong_size = BrandedAssessmentManifest.model_validate(
        {**payload, "uncompressed_size_bytes": int(payload["uncompressed_size_bytes"]) + 1}
    )
    with pytest.raises(ValueError, match="expanded size"):
        verify_archive(archive, wrong_size)


def test_archive_verification_rejects_multiple_members_and_zip_bomb_ratio(
    tmp_path: Path,
) -> None:
    archive, payload = _archive(tmp_path, [_row(1, "036000291452")])
    with ZipFile(archive, "a", compression=ZIP_DEFLATED) as writer:
        writer.writestr("extra.json", "{}")
    multiple_member_manifest = BrandedAssessmentManifest.model_validate(
        {
            **payload,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "size_bytes": archive.stat().st_size,
        }
    )
    with pytest.raises(ValueError, match="exactly one file"):
        verify_archive(archive, multiple_member_manifest)

    compressed = tmp_path / "compressed.zip"
    with ZipFile(compressed, "w", compression=ZIP_DEFLATED) as writer:
        writer.writestr("branded.json", "A" * 100_000)
    with ZipFile(compressed) as reader:
        member = reader.getinfo("branded.json")
    ratio_manifest = BrandedAssessmentManifest.model_validate(
        {
            **payload,
            "filename": compressed.name,
            "sha256": hashlib.sha256(compressed.read_bytes()).hexdigest(),
            "size_bytes": compressed.stat().st_size,
            "uncompressed_size_bytes": member.file_size,
        }
    )
    with pytest.raises(ValueError, match="compression ratio"):
        verify_archive(compressed, ratio_manifest)


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


def test_assessment_reports_eligible_archive_and_scale_limit(tmp_path: Path) -> None:
    archive, payload = _archive(tmp_path, [_row(1, "036000291452")])
    manifest = BrandedAssessmentManifest.model_validate(payload)

    eligible = assess_archive(archive, manifest, workspace=tmp_path)

    assert eligible["eligible_records_after_quarantine"] == 1
    assert eligible["quarantined_records"] == 0
    assert eligible["activation_status"] == "eligible"
    assert eligible["activation_blockers"] == []

    two_rows_path = tmp_path / "two-rows"
    two_rows_path.mkdir()
    two_rows_archive, two_rows_payload = _archive(
        two_rows_path,
        [_row(1, "036000291452"), _row(2, "96385074")],
    )
    limited = BrandedAssessmentManifest.model_validate(
        {**two_rows_payload, "maximum_activation_records": 1}
    )
    held = assess_archive(two_rows_archive, limited, workspace=two_rows_path)

    assert held["activation_status"] == "hold"
    assert held["activation_blockers"] == ["search_projection_scale_limit"]


def test_assessment_quarantines_missing_country_and_exact_duplicates(tmp_path: Path) -> None:
    archive, payload = _archive(
        tmp_path,
        [
            _row(1, "036000291452"),
            _row(2, "036000291452"),
            _row(3, "96385074", country=None),
        ],
    )
    manifest = BrandedAssessmentManifest.model_validate(payload)

    report = assess_archive(archive, manifest, workspace=tmp_path)

    assert report["missing_market_country"] == 1
    assert report["duplicate_identity_groups"] == 1
    assert report["exact_duplicate_identity_groups"] == 1
    assert report["conflicting_identity_groups"] == 0
    assert report["eligible_records_after_quarantine"] == 0
    assert report["quarantined_records"] == 3
    assert report["activation_status"] == "eligible"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"dataType": "Foundation"}, "non-Branded"),
        ({"fdcId": 0}, "invalid FDC ID"),
    ],
)
def test_assessment_rejects_invalid_rows(
    tmp_path: Path, mutation: dict[str, object], message: str
) -> None:
    row = _row(1, "036000291452")
    row.update(mutation)
    archive, payload = _archive(tmp_path, [row])
    manifest = BrandedAssessmentManifest.model_validate(payload)

    with pytest.raises(ValueError, match=message):
        assess_archive(archive, manifest, workspace=tmp_path)


def test_assessment_rejects_maximum_row_overflow(tmp_path: Path) -> None:
    archive, payload = _archive(
        tmp_path,
        [_row(1, "036000291452"), _row(2, "96385074")],
    )
    manifest = BrandedAssessmentManifest.model_validate({**payload, "maximum_rows": 1})

    with pytest.raises(ValueError, match="row count exceeds"):
        assess_archive(archive, manifest, workspace=tmp_path)


def test_assessment_flushes_large_streaming_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = BrandedAssessmentManifest(
        dataset_id="large-fixture",
        filename="unused.zip",
        sha256="0" * 64,
        size_bytes=1,
        json_member="unused.json",
        uncompressed_size_bytes=1,
        expected_rows=10_001,
        maximum_rows=10_001,
        maximum_activation_records=10_001,
    )

    @contextmanager
    def fake_items(
        _path: Path, _manifest: BrandedAssessmentManifest
    ) -> Iterator[Iterator[dict[str, object]]]:
        yield iter(
            {
                "fdcId": index,
                "dataType": "Branded",
                "gtinUpc": None,
            }
            for index in range(1, 10_002)
        )

    monkeypatch.setattr(assessment_module, "verify_archive", lambda *_args: None)
    monkeypatch.setattr(assessment_module, "_items", fake_items)

    report = assess_archive(tmp_path / "unused.zip", manifest, workspace=tmp_path)

    assert report["rows_seen"] == 10_001
    assert report["missing_gtin"] == 10_001
    assert report["quarantined_records"] == 10_001


def test_manifest_loading_and_cli_success_and_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    default_manifest = load_manifest()
    assert default_manifest.dataset_id == "usda-branded-2026-04-30"

    archive, payload = _archive(tmp_path, [_row(1, "036000291452")])
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload))
    output_path = tmp_path / "report.json"

    assert main([str(archive), "--manifest", str(manifest_path), "--output", str(output_path)]) == 0
    assert json.loads(output_path.read_text())["activation_status"] == "eligible"
    assert '"activation_status": "eligible"' in capsys.readouterr().out

    assert main([str(tmp_path / "missing.zip"), "--manifest", str(manifest_path)]) == 2
    assert "assessment failed" in capsys.readouterr().err


def test_manifest_rejects_invalid_sha256() -> None:
    with pytest.raises(ValueError, match="sha256"):
        BrandedAssessmentManifest(
            dataset_id="fixture",
            filename="fixture.zip",
            sha256="INVALID",
            size_bytes=1,
            json_member="fixture.json",
            uncompressed_size_bytes=1,
            maximum_rows=1,
            maximum_activation_records=1,
        )
