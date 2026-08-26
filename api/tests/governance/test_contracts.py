from __future__ import annotations

import pytest
from opennosh_api.governance.contracts import ApprovedChangeSet, ApprovedFileChange


def changes() -> ApprovedChangeSet:
    return ApprovedChangeSet.build(
        pack_id="global-core",
        files=(
            ApprovedFileChange(
                path="packs/global-core/foods/lentils.json",
                content='{"name":"Lentils"}\n',
            ),
            ApprovedFileChange(
                path="packs/global-core/provenance/lentils.json",
                content='{"source":"https://example.test/lentils"}\n',
            ),
        ),
    )


def test_approved_change_set_round_trips_with_deterministic_digest() -> None:
    approved = changes()

    assert ApprovedChangeSet.from_json(approved.as_json()) == approved
    assert len(approved.digest) == 64


@pytest.mark.parametrize(
    "path",
    [
        "/packs/global-core/foods/x.json",
        "packs/global-core/../private/x.json",
        "packs\\global-core\\foods\\x.json",
        "packs/another-pack/foods/x.json",
    ],
)
def test_approved_change_set_cannot_escape_governed_pack(path: str) -> None:
    with pytest.raises(ValueError):
        file = ApprovedFileChange(path=path, content="{}")
        ApprovedChangeSet.build(pack_id="global-core", files=(file,))


def test_approved_change_set_rejects_content_tampering() -> None:
    serialized = changes().as_json()
    serialized["files"][0]["content"] = "tampered"  # type: ignore[index]

    with pytest.raises(ValueError, match="content digest"):
        ApprovedChangeSet.from_json(serialized)


@pytest.mark.parametrize("pack_id", ["Global-Core", "../private", "nested/pack", "pack_"])
def test_approved_change_set_rejects_unsafe_pack_scope(pack_id: str) -> None:
    with pytest.raises(ValueError, match="normalized pack ID"):
        ApprovedChangeSet.build(
            pack_id=pack_id,
            files=(ApprovedFileChange(path="packs/global-core/record.json", content="{}"),),
        )


def test_approved_change_set_rejects_pack_directory_as_file() -> None:
    with pytest.raises(ValueError, match="inside their governed pack"):
        ApprovedChangeSet.build(
            pack_id="global-core",
            files=(ApprovedFileChange(path="packs/global-core", content="invalid"),),
        )
