from __future__ import annotations

from opennosh_api.federation.service import deterministic_equivalence_key


def _record(**updates: object) -> dict[str, object]:
    record: dict[str, object] = {
        "category": "fruit",
        "name": "Apple, raw",
        "source_license": "public-domain",
        "source_uri": "https://fdc.nal.usda.gov/fdc-app.html#/food-details/171688",
    }
    record.update(updates)
    return record


def test_equivalence_requires_an_exact_explicit_source_identity() -> None:
    baseline = deterministic_equivalence_key(_record())

    assert baseline is not None
    assert baseline == deterministic_equivalence_key(_record(name="  APPLE,   RAW "))
    assert baseline != deterministic_equivalence_key(_record(category="juice"))
    assert baseline != deterministic_equivalence_key(
        _record(source_uri="https://fdc.nal.usda.gov/fdc-app.html#/food-details/171689")
    )
    assert deterministic_equivalence_key(_record(source_uri=None)) is None


def test_nutrient_disagreement_does_not_change_equivalence_identity() -> None:
    first = _record(nutrients_json={"energy_kcal": "52"})
    second = _record(nutrients_json={"energy_kcal": "60"})

    assert deterministic_equivalence_key(first) == deterministic_equivalence_key(second)
