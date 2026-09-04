from __future__ import annotations

import pytest
from opennosh_api.reuse.contracts import (
    ReuseDeclarationCreate,
    ReuseDeclarationPatch,
    ReuseRegionLevel,
    normalize_public_url,
    normalized_key,
)
from pydantic import ValidationError


def _declaration(**changes: object) -> ReuseDeclarationCreate:
    values: dict[str, object] = {
        "organization_name": "Community Kitchen",
        "project_name": "Meal Commons",
        "project_url": "https://example.test/projects/meals?view=public",
        "use_case": "Uses verified pack records in a public menu.",
        "region_level": "country",
        "region_code": "US",
    }
    values.update(changes)
    return ReuseDeclarationCreate.model_validate(values)


def test_declaration_normalizes_plain_labels_without_rewriting_display_case() -> None:
    declaration = _declaration(
        organization_name="  Community   Kitchen  ",
        project_name="Meal\u00a0Commons",
    )
    assert declaration.organization_name == "Community Kitchen"
    assert declaration.project_name == "Meal Commons"
    assert normalized_key(declaration.organization_name) == "community kitchen"


@pytest.mark.parametrize(
    ("level", "code"),
    [
        (ReuseRegionLevel.COUNTRY, "us"),
        (ReuseRegionLevel.COUNTRY, "USA"),
        (ReuseRegionLevel.MACROREGION, "01"),
        (ReuseRegionLevel.MACROREGION, "ABC"),
        (None, "US"),
        (ReuseRegionLevel.COUNTRY, None),
    ],
)
def test_declaration_rejects_unbound_or_non_broad_regions(
    level: ReuseRegionLevel | None, code: str | None
) -> None:
    with pytest.raises(ValidationError):
        _declaration(region_level=level, region_code=code)


@pytest.mark.parametrize(
    "value",
    [
        "http://example.test/reuse",
        "https:" + "//user:secret@" + "example.test/reuse",
        "javascript:alert(1)",
        "https://example.test/<unsafe>",
        "https://example.test:99999/reuse",
    ],
)
def test_declaration_rejects_unsafe_project_url_syntax(value: str) -> None:
    with pytest.raises(ValueError, match="public HTTPS URL"):
        normalize_public_url(value)


def test_project_url_is_stored_as_untrusted_text_without_network_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("URL validation must not resolve or fetch")

    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    assert normalize_public_url("https://127.0.0.1/review-only") == (
        "https://127.0.0.1/review-only"
    )


def test_patch_requires_a_change_and_explicit_clear_operations() -> None:
    with pytest.raises(ValidationError, match="must change"):
        ReuseDeclarationPatch()
    with pytest.raises(ValidationError, match="set and cleared"):
        ReuseDeclarationPatch(project_url="https://example.test", clear_project_url=True)
    with pytest.raises(ValidationError, match="set and cleared"):
        ReuseDeclarationPatch(
            region_level=ReuseRegionLevel.COUNTRY,
            region_code="US",
            clear_region=True,
        )
    assert ReuseDeclarationPatch(clear_region=True).clear_region
