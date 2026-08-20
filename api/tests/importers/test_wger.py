from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import opennosh_api.importers.wger as wger_importer
import pytest
from opennosh_api.importers.wger import WgerFormatError, iter_wger

FIXTURES = Path(__file__).parents[1] / "fixtures" / "wger"


def _valid_payload() -> dict[str, object]:
    return json.loads((FIXTURES / "valid.json").read_text())


def _write_payload(tmp_path: Path, payload: object, name: str = "wger.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return path


def test_parser_preserves_complete_attribution_and_plain_text_translations() -> None:
    outcomes = list(iter_wger(FIXTURES / "valid.json"))

    assert all(outcome.record is not None for outcome in outcomes)
    squat = outcomes[0].record
    assert squat is not None
    assert squat.source == "wger"
    assert squat.source_id == "101"
    assert squat.slug == "wger-101"
    assert squat.name == "Barbell Back Squat"
    assert squat.muscle_groups == ["glutes", "quads"]
    assert squat.equipment == ["barbell"]
    assert squat.license_spdx == "CC-BY-SA-3.0"
    assert squat.license_url == "https://creativecommons.org/licenses/by-sa/3.0/"
    assert squat.author == "wger contributors"
    assert squat.author_url == "https://wger.de/"
    assert squat.derivative_source_url == "https://example.org/source/back-squat"
    assert len(squat.translations_json) == 2
    english = squat.translations_json[0]
    assert english["description"] == (
        "Brace your torso and squat with the bar across your upper back."
    )
    assert english["aliases"] == ["Back Squat"]
    assert english["license_spdx"] == "CC-BY-SA-3.0"
    assert english["source_url"] == "https://wger.de/en/exercise/101/view/"
    assert len(squat.translation_attribution_json) == 2


def test_stored_xss_is_removed_and_unsafe_urls_are_rejected() -> None:
    outcomes = list(iter_wger(FIXTURES / "hostile.json"))

    assert outcomes[0].record is not None
    description = outcomes[0].record.translations_json[0]["description"]
    assert description == "Extend the knee under control."
    assert "script" not in str(description).casefold()
    assert "alert" not in str(description).casefold()
    assert outcomes[1].issue is not None
    assert "safe HTTP(S) URL" in outcomes[1].issue.message


@pytest.mark.parametrize(
    ("license_value", "message"),
    [
        (None, "must be an object"),
        (
            {
                "id": 5,
                "short_name": "CC BY-SA",
                "url": "https://creativecommons.org/licenses/by-sa/3.0/",
            },
            "not allowlisted",
        ),
        (
            {
                "id": 6,
                "short_name": "CC-BY-NC-SA 3",
                "url": "https://creativecommons.org/licenses/by-nc-sa/3.0/",
            },
            "not allowlisted",
        ),
        (
            {
                "id": 7,
                "short_name": "CC-BY-ND 3",
                "url": "https://creativecommons.org/licenses/by-nd/3.0/",
            },
            "not allowlisted",
        ),
        (
            {
                "id": 2,
                "short_name": "CC-BY-SA 4",
                "url": "https://creativecommons.org/licenses/by-sa/4.0/",
            },
            "not allowlisted",
        ),
    ],
)
def test_missing_ambiguous_nc_nd_and_unsupported_licenses_are_rejected(
    tmp_path: Path, license_value: object, message: str
) -> None:
    payload = _valid_payload()
    results = payload["results"]
    assert isinstance(results, list)
    exercise = deepcopy(results[0])
    exercise["license"] = license_value

    outcome = next(iter_wger(_write_payload(tmp_path, [exercise])))

    assert outcome.issue is not None
    assert message in outcome.issue.message


def test_mixed_translation_license_rejects_the_whole_exercise(tmp_path: Path) -> None:
    payload = _valid_payload()
    results = payload["results"]
    assert isinstance(results, list)
    exercise = deepcopy(results[0])
    exercise["translations"][1]["license"] = 2

    outcome = next(iter_wger(_write_payload(tmp_path, [exercise])))

    assert outcome.issue is not None
    assert "translations[1].license is not allowlisted" in outcome.issue.message


def test_conflicting_license_name_rejects_ambiguous_metadata(tmp_path: Path) -> None:
    payload = _valid_payload()
    results = payload["results"]
    assert isinstance(results, list)
    exercise = deepcopy(results[0])
    exercise["license"]["full_name"] = "Creative Commons Attribution-NonCommercial 3"

    outcome = next(iter_wger(_write_payload(tmp_path, [exercise])))

    assert outcome.issue is not None
    assert "metadata is ambiguous or unsupported" in outcome.issue.message


def test_url_with_embedded_credentials_is_rejected(tmp_path: Path) -> None:
    payload = _valid_payload()
    results = payload["results"]
    assert isinstance(results, list)
    exercise = deepcopy(results[0])
    exercise["translations"][0]["license_object_url"] = "https://user:pass@wger.de/exercise/101"

    outcome = next(iter_wger(_write_payload(tmp_path, [exercise])))

    assert outcome.issue is not None
    assert "must be a safe HTTP(S) URL" in outcome.issue.message


def test_safe_urls_normalize_uppercase_schemes_before_storage(tmp_path: Path) -> None:
    payload = _valid_payload()
    results = payload["results"]
    assert isinstance(results, list)
    exercise = deepcopy(results[0])
    exercise["translations"][0]["license_author_url"] = "HTTPS://wger.de/"

    outcome = next(iter_wger(_write_payload(tmp_path, [exercise])))

    assert outcome.record is not None
    assert outcome.record.translations_json[0]["author_url"] == "https://wger.de/"


def test_markup_in_author_fields_is_rejected(tmp_path: Path) -> None:
    payload = _valid_payload()
    results = payload["results"]
    assert isinstance(results, list)
    exercise = deepcopy(results[0])
    exercise["license_author"] = '<img src=x onerror="alert(1)">'

    outcome = next(iter_wger(_write_payload(tmp_path, [exercise])))

    assert outcome.issue is not None
    assert "must not contain markup" in outcome.issue.message


@pytest.mark.parametrize(
    "timestamp",
    ["0001-01-01T00:00:00+00:00", "9999-12-31T23:59:59.999999+00:00"],
)
def test_asyncpg_infinity_timestamp_sentinels_are_rejected(
    tmp_path: Path, timestamp: str
) -> None:
    payload = _valid_payload()
    results = payload["results"]
    assert isinstance(results, list)
    exercise = deepcopy(results[0])
    exercise["last_update_global"] = timestamp

    outcome = next(iter_wger(_write_payload(tmp_path, [exercise])))

    assert outcome.issue is not None
    assert "outside the supported timestamp range" in outcome.issue.message


def test_partial_paginated_empty_malformed_and_oversized_exports_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _valid_payload()
    payload["next"] = "https://wger.de/api/v2/exerciseinfo/?offset=2"
    with pytest.raises(WgerFormatError, match="partial paginated export"):
        list(iter_wger(_write_payload(tmp_path, payload, "partial.json")))

    payload["next"] = None
    payload["previous"] = "https://wger.de/api/v2/exerciseinfo/?offset=0"
    with pytest.raises(WgerFormatError, match="partial paginated export"):
        list(iter_wger(_write_payload(tmp_path, payload, "last-page.json")))

    payload["previous"] = None
    payload["count"] = 3
    with pytest.raises(WgerFormatError, match="inconsistent pagination metadata"):
        list(iter_wger(_write_payload(tmp_path, payload, "count-mismatch.json")))

    with pytest.raises(WgerFormatError, match="contains no exercise records"):
        list(iter_wger(_write_payload(tmp_path, [], "empty.json")))

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{")
    with pytest.raises(WgerFormatError, match="malformed JSON"):
        list(iter_wger(malformed))

    invalid_utf8 = tmp_path / "invalid-utf8.json"
    invalid_utf8.write_bytes(b"\xff")
    with pytest.raises(WgerFormatError, match="malformed JSON"):
        list(iter_wger(invalid_utf8))

    oversized = tmp_path / "oversized.json"
    oversized.write_text("[] ")
    monkeypatch.setattr(wger_importer, "_MAX_INPUT_BYTES", 2)
    with pytest.raises(WgerFormatError, match="input limit"):
        list(iter_wger(oversized))


def test_duplicate_source_ids_are_reported_without_hiding_the_first_record(
    tmp_path: Path,
) -> None:
    payload = _valid_payload()
    results = payload["results"]
    assert isinstance(results, list)
    duplicate = [deepcopy(results[0]), deepcopy(results[0])]

    outcomes = list(iter_wger(_write_payload(tmp_path, duplicate)))

    assert outcomes[0].record is not None
    assert outcomes[1].issue is not None
    assert outcomes[1].issue.message == "duplicate wger exercise ID 101"
