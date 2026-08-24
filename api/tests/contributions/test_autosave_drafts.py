from datetime import date, timedelta

import pytest
from opennosh_api.contributions.schemas import (
    ContributionDraftFields,
    ContributionFieldName,
    ContributionFieldPatch,
    ContributionStage,
)
from opennosh_api.contributions.service import (
    _normalize_patch,
    _stage_blockers,
)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (ContributionFieldName.SOURCE_URI, "h"),
        (ContributionFieldName.LOCALE, "en-"),
        (ContributionFieldName.PORTION_AMOUNT, "."),
        (ContributionFieldName.PORTION_GRAMS, "-"),
    ],
)
def test_safe_partial_values_are_preserved_but_do_not_complete_the_stage(
    field: ContributionFieldName,
    value: str,
) -> None:
    normalized = _normalize_patch(ContributionFieldPatch(field=field, value=value))
    assert normalized == value
    fields = ContributionDraftFields.model_validate({field.value: normalized})
    blockers = _stage_blockers(fields, [])
    owner_stage = (
        ContributionStage.EVIDENCE
        if field is ContributionFieldName.SOURCE_URI
        else ContributionStage.DETAILS
    )
    assert any(blocker.field is field for blocker in blockers[owner_stage])


@pytest.mark.parametrize("value", ["NaN", "-1", "1000001"])
def test_invalid_numeric_boundaries_are_preserved_but_block_stage_completion(
    value: str,
) -> None:
    field = ContributionFieldName.PORTION_AMOUNT
    normalized = _normalize_patch(ContributionFieldPatch(field=field, value=value))
    assert normalized == value
    fields = ContributionDraftFields(portion_amount=normalized)
    blockers = _stage_blockers(fields, [])
    assert any(
        blocker.field is field for blocker in blockers[ContributionStage.DETAILS]
    )


def test_future_source_date_is_saved_as_draft_but_blocks_provenance() -> None:
    future = date.today() + timedelta(days=1)
    normalized = _normalize_patch(
        ContributionFieldPatch(
            field=ContributionFieldName.SOURCE_DATE,
            value=future.isoformat(),
        )
    )
    fields = ContributionDraftFields(source_date=normalized)
    blockers = _stage_blockers(fields, [])
    assert any(
        blocker.field is ContributionFieldName.SOURCE_DATE
        for blocker in blockers[ContributionStage.PROVENANCE]
    )


def test_source_credentials_are_never_accepted_into_the_server_draft() -> None:
    with pytest.raises(ValueError, match="public HTTPS URL"):
        _normalize_patch(
            ContributionFieldPatch(
                field=ContributionFieldName.SOURCE_URI,
                value="https://user:password@example.test/source",
            )
        )
