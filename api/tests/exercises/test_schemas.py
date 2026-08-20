from __future__ import annotations

from uuid import uuid4

import pytest
from opennosh_api.exercises.schemas import (
    ExerciseDetail,
    ExerciseTranslation,
    ExerciseTranslationAttribution,
)
from pydantic import ValidationError


def _attribution(**overrides: str) -> dict[str, str]:
    values = {
        "source_id": "101",
        "language_id": "2",
        "source_url": "https://wger.de/api/v2/exercise/101/",
        "license_spdx": "CC-BY-SA-3.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/3.0/",
        "author": "wger contributors",
        "attribution_text": "wger contributors, licensed under CC BY-SA 3.0.",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_url", "javascript:alert(1)"),
        ("source_url", "https://user:pass@wger.de/item"),
        ("source_url", "https://wger.de\\evil"),
        ("license_url", "https://example.test/fake-license"),
        ("license_spdx", "CC-BY-SA-4.0"),
        ("attribution_text", "<script>alert(1)</script>"),
    ],
)
def test_translation_attribution_rejects_unsafe_stored_values(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        ExerciseTranslationAttribution.model_validate(_attribution(**{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("slug", "<img>"),
        ("name", "<script>"),
        ("muscle_groups", ["<img>"]),
        ("equipment", ["<script>"]),
    ],
)
def test_detail_rejects_unsafe_top_level_stored_values(field: str, value: object) -> None:
    payload: dict[str, object] = {
        "id": uuid4(),
        "slug": "wger-101",
        "name": "Squat",
        "muscle_groups": ["quads"],
        "equipment": ["barbell"],
        "translations": [],
        "attribution": {
            "source": "wger",
            "source_id": "101",
            "source_url": "https://wger.de/api/v2/exerciseinfo/101/",
            "license_spdx": "CC-BY-SA-3.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/3.0/",
            "attribution_text": "wger contributors, licensed under CC BY-SA 3.0.",
        },
    }
    payload[field] = value
    with pytest.raises(ValidationError):
        ExerciseDetail.model_validate(payload)


def test_translation_alias_boundary_matches_importer_contract() -> None:
    payload = {
        **_attribution(),
        "source_uuid": None,
        "name": "Squat",
        "description": None,
        "aliases": ["a" * 500],
        "notes": ["n" * 500],
    }
    translation = ExerciseTranslation.model_validate(payload)
    assert len(translation.aliases[0]) == 500
    with pytest.raises(ValidationError):
        ExerciseTranslation.model_validate({**payload, "aliases": ["a" * 501]})
