from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from opennosh_api.contributions.schemas import (
    ContributionDraftFields,
    ContributionEvidenceType,
    ContributionSourceLicense,
)
from opennosh_api.evidence.contracts import DocumentRightsState, PublicDocumentManifest
from opennosh_api.first_contribution.contracts import (
    FIRST_FDC_ID,
    FIRST_PACK_ID,
    FIRST_RECORD_ID,
    FIRST_SOURCE_URI,
    FirstContributionPackage,
    canonical_json,
    derived_id,
)
from opennosh_api.governance.contracts import ApprovedChangeSet, ApprovedFileChange

MAX_SOURCE_BYTES = 2 * 1024 * 1024
OBSERVED_AT = datetime(2026, 8, 28, 16, 0, tzinfo=UTC)
EXPECTED_DESCRIPTION = "Bananas, ripe and slightly ripe, raw"
EXPECTED_PUBLICATION_DATE = date(2020, 4, 1)
EXPECTED_NUTRIENTS = {
    1008: Decimal("97"),
    1003: Decimal("0.74"),
    1004: Decimal("0.29"),
    1005: Decimal("23.0"),
}


class FirstContributionPreparationError(ValueError):
    pass


def prepare_usda_first_contribution(
    source_path: Path,
    output_path: Path,
) -> FirstContributionPackage:
    source = _read_source(source_path)
    _validate_source(source)
    source_digest = hashlib.sha256(canonical_json(source)).hexdigest()
    package = _build_package(source_digest)
    payload = package.canonical_bytes()
    if output_path.exists():
        metadata = output_path.lstat()
        if (
            output_path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise FirstContributionPreparationError(
                "First-contribution output must be a mode-0600 regular file"
            )
        if _read_regular_file(output_path, limit=MAX_SOURCE_BYTES) != payload:
            raise FirstContributionPreparationError(
                "First-contribution output already exists with different bytes"
            )
        return package
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    return package


def load_first_contribution_package(path: Path) -> FirstContributionPackage:
    payload = _read_regular_file(path, limit=MAX_SOURCE_BYTES)
    try:
        package = FirstContributionPackage.model_validate_json(payload)
    except ValueError as error:
        raise FirstContributionPreparationError("First-contribution package is invalid") from error
    return validate_first_contribution_package(package)


def validate_first_contribution_package(
    package: FirstContributionPackage,
) -> FirstContributionPackage:
    expected = _build_package(package.source_record_digest)
    if package.canonical_bytes() != expected.canonical_bytes():
        raise FirstContributionPreparationError(
            "First-contribution package differs from the pinned reviewed material"
        )
    return package


def _read_source(path: Path) -> dict[str, Any]:
    payload = _read_regular_file(path, limit=MAX_SOURCE_BYTES)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FirstContributionPreparationError("USDA source must be valid JSON") from error
    if not isinstance(value, dict):
        raise FirstContributionPreparationError("USDA source must be a JSON object")
    return value


def _read_regular_file(path: Path, *, limit: int) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise FirstContributionPreparationError("Input file could not be inspected") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise FirstContributionPreparationError("Input must be a regular non-symlink file")
    if metadata.st_size <= 0 or metadata.st_size > limit:
        raise FirstContributionPreparationError(f"Input must be between 1 and {limit} bytes")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise FirstContributionPreparationError("Input file could not be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
            or opened.st_size != metadata.st_size
        ):
            raise FirstContributionPreparationError("Input changed before it was opened")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise FirstContributionPreparationError("Input changed while it was being read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise FirstContributionPreparationError("Input grew while it was being read")
        finished = os.fstat(descriptor)
        if (
            finished.st_size != opened.st_size
            or finished.st_mtime_ns != opened.st_mtime_ns
            or finished.st_ctime_ns != opened.st_ctime_ns
        ):
            raise FirstContributionPreparationError("Input changed while it was being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _validate_source(source: dict[str, Any]) -> None:
    if str(source.get("fdcId")) != FIRST_FDC_ID:
        raise FirstContributionPreparationError("USDA source FDC ID is not pinned record 1105314")
    if source.get("dataType") != "Foundation":
        raise FirstContributionPreparationError("USDA source must be Foundation data")
    if source.get("description") != EXPECTED_DESCRIPTION:
        raise FirstContributionPreparationError("USDA source description differs from the pin")
    if source.get("publicationDate") != EXPECTED_PUBLICATION_DATE.isoformat():
        raise FirstContributionPreparationError("USDA source publication date differs from the pin")
    observed: dict[int, Decimal] = {}
    raw_nutrients = source.get("foodNutrients")
    if not isinstance(raw_nutrients, list) or len(raw_nutrients) > 512:
        raise FirstContributionPreparationError("USDA source nutrients are missing or unbounded")
    for item in raw_nutrients:
        if not isinstance(item, dict) or not isinstance(item.get("nutrient"), dict):
            continue
        nutrient_id = item["nutrient"].get("id")
        amount = item.get("amount")
        if nutrient_id in EXPECTED_NUTRIENTS and amount is not None:
            normalized_id = int(nutrient_id)
            if normalized_id in observed:
                raise FirstContributionPreparationError(
                    "USDA source contains a duplicate pinned nutrient"
                )
            try:
                observed[normalized_id] = Decimal(str(amount))
            except ArithmeticError as error:
                raise FirstContributionPreparationError(
                    "USDA source nutrient amount is invalid"
                ) from error
    if observed != EXPECTED_NUTRIENTS:
        raise FirstContributionPreparationError("USDA source nutrient values differ from the pin")


def _build_package(source_digest: str) -> FirstContributionPackage:
    evidence_id = derived_id(source_digest, "evidence")
    evidence = PublicDocumentManifest(
        evidence_id=evidence_id,
        canonical_uri=FIRST_SOURCE_URI,
        publisher="USDA FoodData Central",
        license="CC0-1.0",
        title=f"USDA FoodData Central record {FIRST_FDC_ID}: {EXPECTED_DESCRIPTION}",
        observed_at=OBSERVED_AT,
        observed_digest=source_digest,
        rights_state=DocumentRightsState.REFERENCE_ONLY,
    )
    fields = ContributionDraftFields(
        evidence_type=ContributionEvidenceType.PUBLIC_DOCUMENT,
        source_uri=FIRST_SOURCE_URI,
        rights_acknowledged=True,
        name=EXPECTED_DESCRIPTION,
        category="fruit",
        portion_description="100 g",
        portion_amount="100",
        portion_unit="g",
        portion_grams="100",
        energy_kcal="97",
        protein_g="0.74",
        fat_g="0.29",
        carbohydrate_g="23.0",
        duplicates_resolved=True,
        pack_id=FIRST_PACK_ID,
        source_date=EXPECTED_PUBLICATION_DATE,
        attribution="USDA FoodData Central",
        source_license=ContributionSourceLicense.CC0,
        review_acknowledged=True,
    )
    changes = ApprovedChangeSet.build(
        pack_id=FIRST_PACK_ID,
        files=(
            ApprovedFileChange(path=f"packs/{FIRST_PACK_ID}/README.md", content=_readme()),
            ApprovedFileChange(
                path=f"packs/{FIRST_PACK_ID}/foods/foods.yaml", content=_foods_yaml()
            ),
            ApprovedFileChange(path=f"packs/{FIRST_PACK_ID}/pack.yaml", content=_pack_yaml()),
        ),
    )
    material: dict[str, Any] = {
        "schema_version": "1.0",
        "fdc_id": FIRST_FDC_ID,
        "source_record_digest": source_digest,
        "source_actor_id": str(derived_id(source_digest, "source-actor")),
        "draft_id": str(derived_id(source_digest, "draft")),
        "submission_id": str(derived_id(source_digest, "submission")),
        "evidence_id": str(evidence_id),
        "role_assignment_id": str(derived_id(source_digest, "steward-role")),
        "decision_id": str(derived_id(source_digest, "decision")),
        "publication_intent_id": str(derived_id(source_digest, "publication-intent")),
        "draft_fields": fields.model_dump(mode="json"),
        "evidence_manifest": evidence.model_dump(mode="json"),
        "approved_changes": changes.as_json(),
        "record_id": FIRST_RECORD_ID,
    }
    material["package_digest"] = hashlib.sha256(canonical_json(material)).hexdigest()
    return FirstContributionPackage.model_validate(material)


def _pack_yaml() -> str:
    return """id: common-fruits
name: Common fruits
description: Generic fruit records with transparent public provenance for everyday logging.
version: 1.0.0
locale: en
license: CC0-1.0
maintainers:
- github: RujitRaval
entry_count: 1
"""


def _foods_yaml() -> str:
    return """- slug: bananas-ripe-and-slightly-ripe-raw
  name: Bananas, ripe and slightly ripe, raw
  category: fruit
  tags:
  - fruit
  - usda
  contributed_by: USDA-FoodData-Central
  provenance: government_database
  source_uri: https://fdc.nal.usda.gov/fdc-app.html#/food-details/1105314/nutrients
  source_license: CC0-1.0
  source_note: >-
    USDA FoodData Central Foundation record 1105314, published 2020-04-01.
    Nutrient values are reproduced from the public-domain record.
  basis: per_100g
  nutrients:
    energy_kcal: 97
    protein_g: 0.74
    fat_g: 0.29
    carbohydrate_g: 23.0
  portions:
  - name: 100 g
    grams: 100
"""


def _readme() -> str:
    return """# Common fruits

Generic fruit records with transparent public provenance for everyday logging.

## Data method

This initial record reproduces USDA FoodData Central Foundation nutrient values on a
per-100-gram basis. USDA data is public domain and is represented as `CC0-1.0` in the
opennosh pack schema. The named 100 g portion is an exact mass basis, not a household-serving
estimate.

This pack is a practical logging reference, not clinical advice. Variety, ripeness, preparation,
and measurement can change nutrient values. Prefer a product label or laboratory result when it
better represents the food actually eaten.
"""
