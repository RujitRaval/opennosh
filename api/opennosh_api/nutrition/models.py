from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from copy import deepcopy
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal, DecimalException, localcontext
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    RootModel,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

_NUTRIENT_CODE = re.compile(r"^[a-z][a-z0-9_]{0,54}_(?:g|mg|mcg|kcal|kj|iu)$")
_REQUIRED_MACROS = frozenset({"energy_kcal", "protein_g", "fat_g", "carbohydrate_g"})
_MAX_DENSITY_G_PER_ML = Decimal("5")
_MIN_DENSITY_G_PER_ML = Decimal("0.01")
_MAX_PORTION_GRAMS = Decimal("10000")
_MAX_QUANTITY = Decimal("1000000")
_MAX_RAW_NUTRIENT_VALUE = Decimal("1e18")
_MAX_CANONICAL_VALUE_BY_UNIT = {
    "g": Decimal("100"),
    "mg": Decimal("100000"),
    "mcg": Decimal("100000000"),
    "kcal": Decimal("900"),
    "kj": Decimal("3766"),
    "iu": Decimal("10000000000"),
}
_MAX_AUTHORITATIVE_ENERGY_BY_UNIT = {
    "kcal": Decimal("1000"),
    "kj": Decimal("4184"),
}
_ENERGY_TOLERANCE = Decimal("0.15")
_ARITHMETIC_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)


class NutrientBasis(StrEnum):
    PER_100G = "per_100g"
    PER_100ML = "per_100ml"


class QuantityUnit(StrEnum):
    GRAM = "g"
    MILLILITRE = "ml"
    NAMED_PORTION = "portion"


def _finite_positive(value: Decimal, *, label: str, maximum: Decimal) -> Decimal:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{label} must be finite and greater than zero")
    if value > maximum:
        raise ValueError(f"{label} must not exceed {maximum}")
    return value


def _optional_density(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    density = _finite_positive(
        value,
        label="Density in grams per millilitre",
        maximum=_MAX_DENSITY_G_PER_ML,
    )
    if density < _MIN_DENSITY_G_PER_ML:
        raise ValueError(
            f"Density in grams per millilitre must be at least {_MIN_DENSITY_G_PER_ML}"
        )
    return density


def deterministic_multiply(left: Decimal, right: Decimal) -> Decimal:
    """Multiply under opennosh's fixed decimal context."""
    try:
        with localcontext(_ARITHMETIC_CONTEXT):
            result = left * right
    except DecimalException as error:
        raise ValueError("Decimal multiplication exceeds the supported numeric range") from error
    if not result.is_finite():
        raise ValueError("Decimal multiplication must produce a finite value")
    return result


def deterministic_add(left: Decimal, right: Decimal) -> Decimal:
    """Add under opennosh's fixed decimal context."""
    try:
        with localcontext(_ARITHMETIC_CONTEXT):
            result = left + right
    except DecimalException as error:
        raise ValueError("Decimal addition exceeds the supported numeric range") from error
    if not result.is_finite():
        raise ValueError("Decimal addition must produce a finite value")
    return result


def deterministic_divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Divide under opennosh's fixed decimal context."""
    try:
        with localcontext(_ARITHMETIC_CONTEXT):
            result = numerator / denominator
    except DecimalException as error:
        raise ValueError("Decimal division exceeds the supported numeric range") from error
    if not result.is_finite():
        raise ValueError("Decimal division must produce a finite value")
    return result


def _validate_basis_limits(
    nutrients: NutrientValues, *, authoritative_source: bool = False
) -> None:
    for code, amount in nutrients.items():
        unit = code.rsplit("_", maxsplit=1)[1]
        maximum = (
            _MAX_AUTHORITATIVE_ENERGY_BY_UNIT.get(
                unit, _MAX_CANONICAL_VALUE_BY_UNIT[unit]
            )
            if authoritative_source
            else _MAX_CANONICAL_VALUE_BY_UNIT[unit]
        )
        if amount > maximum:
            raise ValueError(f"{code} cannot exceed {maximum} per 100g")


class NutrientValues(RootModel[Mapping[str, Decimal]]):
    """An immutable, validated nutrient-code-to-value mapping."""

    model_config = ConfigDict(frozen=True)

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        schema = handler(core_schema)
        value_schema = deepcopy(schema["additionalProperties"])
        if isinstance(value_schema, dict):
            for option in value_schema.get("anyOf", []):
                if option.get("type") == "number":
                    option["minimum"] = 0
                    option["maximum"] = int(_MAX_RAW_NUTRIENT_VALUE)
        schema.update(
            {
                "additionalProperties": value_schema,
                "description": (
                    "Nutrient values must be finite, non-negative, and no greater than 1e18. "
                    "The four core macros are required, and macro-derived energy must be within "
                    "15% of energy_kcal. Canonical profiles apply tighter unit-specific bounds."
                ),
                "properties": {
                    code: deepcopy(value_schema) for code in sorted(_REQUIRED_MACROS)
                },
                "propertyNames": {"pattern": _NUTRIENT_CODE.pattern},
                "required": sorted(_REQUIRED_MACROS),
            }
        )
        return schema

    @field_validator("root", mode="before")
    @classmethod
    def reject_boolean_values(cls, value: object) -> object:
        if isinstance(value, Mapping) and any(isinstance(item, bool) for item in value.values()):
            raise ValueError("Nutrient values must be numbers, not booleans")
        return value

    @field_validator("root")
    @classmethod
    def validate_and_freeze(
        cls, value: Mapping[str, Decimal], info: ValidationInfo
    ) -> Mapping[str, Decimal]:
        missing = sorted(_REQUIRED_MACROS - value.keys())
        if missing:
            raise ValueError(f"Missing required nutrients: {', '.join(missing)}")

        validated: dict[str, Decimal] = {}
        for code, amount in value.items():
            if not _NUTRIENT_CODE.fullmatch(code):
                raise ValueError(f"Invalid nutrient code: {code}")
            if not amount.is_finite() or amount < 0:
                raise ValueError(f"{code} must be finite and non-negative")
            if amount > _MAX_RAW_NUTRIENT_VALUE:
                raise ValueError(f"{code} exceeds the supported numeric range")
            validated[code] = amount

        energy = validated["energy_kcal"]
        try:
            with localcontext(_ARITHMETIC_CONTEXT):
                calculated_energy = (
                    Decimal(4) * validated["protein_g"]
                    + Decimal(4) * validated["carbohydrate_g"]
                    + Decimal(9) * validated["fat_g"]
                )
                if energy == 0:
                    mismatch = calculated_energy != 0
                else:
                    mismatch = abs(calculated_energy - energy) / energy > _ENERGY_TOLERANCE
        except DecimalException as error:
            raise ValueError("Nutrient arithmetic exceeds the supported numeric range") from error
        authoritative_source = bool(
            info.context and info.context.get("authoritative_source") is True
        )
        if mismatch and not authoritative_source:
            raise ValueError("Macro-derived energy differs from energy_kcal by more than 15%")

        return MappingProxyType(dict(sorted(validated.items())))

    @classmethod
    def from_authoritative_source(cls, value: Mapping[str, Decimal]) -> NutrientValues:
        """Validate exact published values that may use food-specific energy factors."""
        return cls.model_validate(value, context={"authoritative_source": True})

    @model_serializer
    def serialize(self) -> dict[str, Decimal]:
        return dict(self.root)

    def __getitem__(self, code: str) -> Decimal:
        return self.root[code]

    def codes(self) -> Iterator[str]:
        return iter(self.root)

    def items(self) -> Iterator[tuple[str, Decimal]]:
        return iter(self.root.items())

    def scaled(self, factor: Decimal) -> NutrientValues:
        if not factor.is_finite() or factor <= 0:
            raise ValueError("Nutrient scale factor must be finite and greater than zero")
        # Proportional scaling cannot introduce a new macro/energy mismatch. Preserve
        # authoritative profiles that were already validated at the source boundary.
        return NutrientValues.model_validate(
            {code: deterministic_multiply(amount, factor) for code, amount in self.items()},
            context={"authoritative_source": True},
        )


class HouseholdPortion(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    grams: Decimal

    @field_validator("grams")
    @classmethod
    def validate_grams(cls, value: Decimal) -> Decimal:
        return _finite_positive(value, label="Portion grams", maximum=_MAX_PORTION_GRAMS)


class Quantity(BaseModel):
    """A mass, volume, or count of one named household portion."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    amount: Decimal
    unit: QuantityUnit
    portion_name: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        return _finite_positive(value, label="Quantity", maximum=_MAX_QUANTITY)

    @model_validator(mode="after")
    def validate_portion_name(self) -> Quantity:
        if self.unit is QuantityUnit.NAMED_PORTION and self.portion_name is None:
            raise ValueError("Named-portion quantities require portion_name")
        if self.unit is not QuantityUnit.NAMED_PORTION and self.portion_name is not None:
            raise ValueError("portion_name is only valid for named-portion quantities")
        return self


class DeclaredNutrients(BaseModel):
    """Nutrients as declared by a source before canonicalisation."""

    model_config = ConfigDict(frozen=True)

    basis: NutrientBasis
    nutrients: NutrientValues
    density_g_per_ml: Decimal | None = None

    @field_validator("density_g_per_ml")
    @classmethod
    def validate_density(cls, value: Decimal | None) -> Decimal | None:
        return _optional_density(value)

    @model_validator(mode="after")
    def validate_declared_basis(self) -> DeclaredNutrients:
        if self.basis is NutrientBasis.PER_100ML and self.density_g_per_ml is None:
            raise ValueError("per_100ml nutrients require density_g_per_ml")
        _validate_basis_limits(self._canonical_nutrients())
        return self

    def _canonical_nutrients(self) -> NutrientValues:
        if self.basis is NutrientBasis.PER_100ML:
            if self.density_g_per_ml is None:  # pragma: no cover - enforced at validation
                raise ValueError("per_100ml nutrients require density_g_per_ml")
            factor = deterministic_divide(Decimal(1), self.density_g_per_ml)
            return self.nutrients.scaled(factor) if factor != 1 else self.nutrients
        return self.nutrients

    def to_canonical(self) -> NutrientProfile:
        return NutrientProfile(
            nutrients=self._canonical_nutrients(),
            density_g_per_ml=self.density_g_per_ml,
        )


class NutrientProfile(BaseModel):
    """The single internal storage basis used by calculations and persistence."""

    model_config = ConfigDict(frozen=True)

    basis: Literal[NutrientBasis.PER_100G] = NutrientBasis.PER_100G
    nutrients: NutrientValues
    density_g_per_ml: Decimal | None = None

    @field_validator("density_g_per_ml")
    @classmethod
    def validate_density(cls, value: Decimal | None) -> Decimal | None:
        return _optional_density(value)

    @model_validator(mode="after")
    def validate_canonical_basis(self, info: ValidationInfo) -> NutrientProfile:
        authoritative_source = bool(
            info.context and info.context.get("authoritative_source") is True
        )
        _validate_basis_limits(
            self.nutrients, authoritative_source=authoritative_source
        )
        return self

    @classmethod
    def from_authoritative_source(
        cls, nutrients: Mapping[str, Decimal]
    ) -> NutrientProfile:
        """Validate published per-100g values with bounded source tolerances."""
        return cls.model_validate(
            {"nutrients": NutrientValues.from_authoritative_source(nutrients)},
            context={"authoritative_source": True},
        )


class NutrientSnapshot(BaseModel):
    """An immutable copy of calculated nutrients for a consumed mass."""

    model_config = ConfigDict(frozen=True)

    basis: Literal["computed"] = "computed"
    grams: Decimal
    nutrients: NutrientValues

    @field_validator("grams")
    @classmethod
    def validate_grams(cls, value: Decimal) -> Decimal:
        return _finite_positive(value, label="Snapshot grams", maximum=_MAX_QUANTITY)

    def rounded_for_api(self, decimal_places: int = 2) -> NutrientSnapshotPayload:
        """Return a rounded boundary representation without mutating stored values."""
        if not 0 <= decimal_places <= 6:
            raise ValueError("decimal_places must be between 0 and 6")
        quantum = Decimal(1).scaleb(-decimal_places)
        try:
            with localcontext(_ARITHMETIC_CONTEXT):
                return NutrientSnapshotPayload(
                    basis=self.basis,
                    grams=self.grams.quantize(quantum, rounding=ROUND_HALF_UP),
                    nutrients={
                        code: amount.quantize(quantum, rounding=ROUND_HALF_UP)
                        for code, amount in self.nutrients.items()
                    },
                )
        except DecimalException as error:
            raise ValueError(
                "Rounded nutrient payload exceeds the supported numeric range"
            ) from error


class NutrientSnapshotPayload(BaseModel):
    """Rounded API payload with decimals serialized as exact JSON strings."""

    model_config = ConfigDict(frozen=True)

    basis: Literal["computed"] = "computed"
    grams: Decimal
    nutrients: Mapping[str, Decimal]

    @field_validator("nutrients")
    @classmethod
    def freeze_nutrients(cls, value: Mapping[str, Decimal]) -> Mapping[str, Decimal]:
        return MappingProxyType(dict(value))

    @field_serializer("grams", when_used="json")
    def serialize_grams(self, value: Decimal) -> str:
        return format(value, "f")

    @field_serializer("nutrients", when_used="json")
    def serialize_nutrients(self, value: Mapping[str, Decimal]) -> dict[str, str]:
        return {code: format(amount, "f") for code, amount in value.items()}
