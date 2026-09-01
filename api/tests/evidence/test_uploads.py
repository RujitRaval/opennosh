from uuid import UUID

import pytest
from opennosh_api.evidence.uploads import (
    EVIDENCE_UPLOAD_MAX_BYTES,
    EvidenceUploadPolicyError,
    EvidenceUploadState,
    hash_secret,
    issue_upload_capability,
    require_t34_1_transition,
    upload_request_hash,
    validate_upload_declaration,
)

USER_ID = UUID("00000000-0000-4000-8000-000000000001")
DRAFT_ID = UUID("00000000-0000-4000-8000-000000000002")


@pytest.mark.parametrize("media_type", ["image/jpeg", "image/png", "image/webp"])
def test_supported_upload_declarations_are_canonical(media_type: str) -> None:
    assert validate_upload_declaration(media_type.upper(), 1) == media_type


@pytest.mark.parametrize(
    ("media_type", "byte_length", "code"),
    [
        ("image/gif", 1, "media_type_unsupported"),
        ("image/png", 0, "byte_length_out_of_range"),
        ("image/png", EVIDENCE_UPLOAD_MAX_BYTES + 1, "byte_length_out_of_range"),
    ],
)
def test_upload_declaration_rejects_untrusted_bounds(
    media_type: str, byte_length: int, code: str
) -> None:
    with pytest.raises(EvidenceUploadPolicyError, match=code):
        validate_upload_declaration(media_type, byte_length)

    with pytest.raises(EvidenceUploadPolicyError, match="upload_max_bytes_out_of_range"):
        validate_upload_declaration("image/png", 1, max_bytes=0)


def test_upload_capability_is_opaque_and_only_its_digest_is_stable() -> None:
    first = issue_upload_capability()
    second = issue_upload_capability()

    assert first.value != second.value
    assert len(first.value) == 43
    assert first.digest == hash_secret(first.value)
    assert len(first.digest) == 64
    assert first.value not in first.digest
    with pytest.raises(EvidenceUploadPolicyError, match="secret_empty"):
        hash_secret("")


def test_upload_request_hash_is_canonical_and_scope_bound() -> None:
    first = upload_request_hash(
        user_id=USER_ID,
        draft_id=DRAFT_ID,
        source_draft_version=1,
        media_type=" IMAGE/PNG ",
        byte_length=25,
    )
    replay = upload_request_hash(
        user_id=USER_ID,
        draft_id=DRAFT_ID,
        source_draft_version=1,
        media_type="image/png",
        byte_length=25,
    )
    changed = upload_request_hash(
        user_id=USER_ID,
        draft_id=DRAFT_ID,
        source_draft_version=2,
        media_type="image/png",
        byte_length=25,
    )

    assert first == replay
    assert first != changed
    with pytest.raises(EvidenceUploadPolicyError, match="source_draft_version_invalid"):
        upload_request_hash(
            user_id=USER_ID,
            draft_id=DRAFT_ID,
            source_draft_version=0,
            media_type="image/png",
            byte_length=25,
        )


@pytest.mark.parametrize(
    "target",
    [EvidenceUploadState.UPLOADED, EvidenceUploadState.EXPIRED, EvidenceUploadState.FAILED],
)
def test_t34_1_only_allows_initiated_terminal_transitions(
    target: EvidenceUploadState,
) -> None:
    require_t34_1_transition(EvidenceUploadState.INITIATED, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (EvidenceUploadState.UPLOADED, EvidenceUploadState.SANITIZING),
        (EvidenceUploadState.EXPIRED, EvidenceUploadState.FAILED),
        (EvidenceUploadState.FAILED, EvidenceUploadState.INITIATED),
        (EvidenceUploadState.INITIATED, EvidenceUploadState.INITIATED),
    ],
)
def test_t34_1_rejects_later_or_revival_transitions(
    current: EvidenceUploadState, target: EvidenceUploadState
) -> None:
    with pytest.raises(EvidenceUploadPolicyError, match="state_transition_invalid"):
        require_t34_1_transition(current, target)
