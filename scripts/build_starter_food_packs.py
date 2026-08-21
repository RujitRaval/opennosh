#!/usr/bin/env python3
"""Build opennosh's four starter food packs from pinned USDA bulk releases.

The generated YAML is committed so installing opennosh never requires network access.
Run this script only when intentionally refreshing the curated source records.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import ijson  # type: ignore[import-untyped]
import yaml

CONTRIBUTOR = "RujitRaval"
FDC_URI = "https://fdc.nal.usda.gov/fdc-app.html#/food-details/{fdc_id}/nutrients"


@dataclass(frozen=True)
class Dataset:
    key: str
    label: str
    release: str
    root: str


FNDDS = Dataset("fndds", "FNDDS 2021–2023", "2024-10-31", "SurveyFoods")
SR_LEGACY = Dataset("sr", "SR Legacy", "2018-04", "SRLegacyFoods")


@dataclass(frozen=True)
class SourceRef:
    dataset: Dataset
    fdc_id: int


@dataclass(frozen=True)
class Component:
    fdc_id: int
    grams: Decimal


@dataclass(frozen=True)
class Recipe:
    slug: str
    name: str
    category: str
    components: tuple[Component, ...]
    yield_grams: Decimal
    portion_name: str
    portion_grams: Decimal
    tags: tuple[str, ...] = ("vegetarian", "indian", "gujarati")


def grams(value: int | str) -> Decimal:
    return Decimal(str(value))


GUJARATI_DIRECT_IDS = (
    2705393,
    2705418,
    2705686,
    2705700,
    2707427,
    2707431,
    2707432,
    2707713,
    2708346,
    2708347,
    2708985,
    2709030,
    2709031,
    2709128,
    2709129,
    2709309,
    2710066,
    2710067,
    2710068,
    2709615,
    2709618,
    2709664,
    2709692,
    2709721,
    2709889,
    2709902,
    2709930,
    2709946,
    2709950,
    2710026,
)

NORTH_DIRECT_IDS = (
    # Rice and cooked grains.
    2708403,
    2708404,
    2708405,
    2708409,
    2708410,
    2708411,
    2708415,
    2708416,
    2708417,
    2708419,
    2708421,
    2708434,
    # Flatbreads and wheat staples.
    2707613,
    2707714,
    2707715,
    2707709,
    2707711,
    2707718,
    2707730,
    2707790,
    # Cooked legumes.
    2707360,
    2707361,
    2707372,
    2707373,
    2707380,
    2707381,
    2707383,
    2707413,
    2707415,
    2707416,
    2707418,
    2707420,
    2707421,
    2707424,
    2707425,
    # Vegetables prepared in common household forms.
    2709623,
    2709619,
    2709660,
    2709667,
    2709691,
    2709699,
    2709700,
    2709719,
    2709724,
    2709773,
    2709903,
    2710049,
    2709948,
    2709951,
    2710027,
    # Dairy and recognizable dishes.
    2705385,
    2705417,
    2705420,
    2705749,
    2705685,
    2709631,
    2708730,
    2706437,
    2706460,
    2706388,
)

PROTEIN_SR_IDS = (
    167703,
    168147,
    168410,
    168114,
    172422,
    168498,
    172426,
    169283,
    169328,
    170156,
    170162,
    170184,
    170556,
    170562,
    170567,
    170894,
    172179,
    172181,
    172186,
    171304,
    173424,
    172448,
    172449,
    172467,
    173728,
    172430,
    170155,
    174271,
)

SUPPLEMENT_SR_IDS = (
    168893,
    169414,
    169714,
    170041,
    169599,
    172231,
    174273,
    170148,
    174274,
    170554,
    170657,
    170876,
    170877,
    170895,
    171281,
    171283,
    172204,
    172435,
    172444,
    173177,
    173180,
    173181,
    174288,
    174267,
    174275,
)

# Component labels are intentionally short because they are copied into source_note.
INGREDIENTS = {
    168147: "vital wheat gluten",
    168462: "spinach",
    168893: "whole-wheat flour",
    169228: "eggplant",
    169231: "ginger",
    169260: "okra",
    169655: "granulated sugar",
    169714: "rice flour",
    169756: "white rice",
    169975: "cabbage",
    169997: "cilantro",
    170000: "onion",
    170026: "potato",
    170150: "sesame seed",
    170169: "coconut",
    170393: "carrot",
    170457: "tomato",
    170487: "summer squash",
    171284: "plain whole-milk yogurt",
    171314: "ghee",
    171324: "fenugreek seed",
    172231: "turmeric",
    172336: "canola oil",
    172420: "lentils",
    172430: "peanuts",
    172436: "pigeon peas",
    174256: "mung beans",
    174288: "chickpea flour",
}


def component(fdc_id: int, weight: int | str) -> Component:
    return Component(fdc_id, grams(weight))


GUJARATI_RECIPES = (
    Recipe(
        "gujarati-plain-thepla",
        "Plain thepla",
        "bread",
        (
            component(168893, 100),
            component(171284, 30),
            component(172336, 12),
            component(171324, 3),
            component(172231, 1),
        ),
        grams(175),
        "1 thepla",
        grams(44),
    ),
    Recipe(
        "gujarati-besan-chilla",
        "Besan chilla",
        "bread",
        (
            component(174288, 100),
            component(170000, 20),
            component(170457, 20),
            component(172336, 8),
        ),
        grams(230),
        "1 chilla",
        grams(58),
    ),
    Recipe(
        "gujarati-dhokla",
        "Dhokla",
        "snack",
        (
            component(174288, 100),
            component(171284, 40),
            component(172336, 8),
            component(170150, 3),
        ),
        grams(245),
        "1 piece",
        grams(31),
    ),
    Recipe(
        "gujarati-khaman",
        "Khaman",
        "snack",
        (
            component(174288, 100),
            component(172336, 10),
            component(170150, 3),
        ),
        grams(225),
        "1 piece",
        grams(28),
    ),
    Recipe(
        "gujarati-handvo",
        "Handvo",
        "savory_cake",
        (
            component(169756, 60),
            component(174256, 40),
            component(171284, 40),
            component(170487, 50),
            component(172336, 10),
        ),
        grams(305),
        "1 wedge",
        grams(76),
    ),
    Recipe(
        "gujarati-khandvi",
        "Khandvi",
        "snack",
        (
            component(174288, 80),
            component(171284, 200),
            component(172336, 8),
            component(170150, 3),
        ),
        grams(265),
        "6 rolls",
        grams(66),
    ),
    Recipe(
        "gujarati-dudhi-muthiya",
        "Dudhi muthiya (summer-squash model)",
        "snack",
        (
            component(168893, 40),
            component(174288, 60),
            component(170487, 70),
            component(172336, 8),
        ),
        grams(225),
        "4 muthiya pieces",
        grams(56),
    ),
    Recipe(
        "gujarati-moong-khichdi",
        "Moong dal khichdi",
        "rice_dish",
        (
            component(169756, 80),
            component(174256, 60),
            component(172336, 5),
            component(172231, 1),
        ),
        grams(425),
        "1 katori",
        grams(180),
    ),
    Recipe(
        "gujarati-vaghareli-khichdi",
        "Vaghareli khichdi",
        "rice_dish",
        (
            component(169756, 80),
            component(174256, 60),
            component(170393, 40),
            component(170000, 30),
            component(172336, 12),
        ),
        grams(475),
        "1 katori",
        grams(180),
    ),
    Recipe(
        "gujarati-tuvar-dal",
        "Gujarati tuvar dal",
        "dal",
        (
            component(172436, 100),
            component(170457, 50),
            component(170000, 30),
            component(172336, 8),
            component(172231, 1),
        ),
        grams(405),
        "1 katori",
        grams(180),
    ),
    Recipe(
        "gujarati-kadhi",
        "Gujarati kadhi",
        "dal",
        (
            component(171284, 300),
            component(174288, 30),
            component(172336, 8),
            component(169231, 4),
        ),
        grams(365),
        "1 katori",
        grams(180),
    ),
    Recipe(
        "gujarati-dal-dhokli",
        "Dal dhokli",
        "one_pot_meal",
        (
            component(172436, 80),
            component(168893, 80),
            component(170457, 40),
            component(172336, 10),
        ),
        grams(525),
        "1 bowl",
        grams(260),
    ),
    Recipe(
        "gujarati-undhiyu",
        "Undhiyu (mixed-vegetable model)",
        "vegetable_dish",
        (
            component(169228, 100),
            component(170026, 100),
            component(170487, 100),
            component(172430, 20),
            component(170169, 20),
            component(172336, 20),
        ),
        grams(335),
        "1 katori",
        grams(180),
    ),
    Recipe(
        "gujarati-sev-tameta",
        "Sev tameta (besan-sev model)",
        "vegetable_dish",
        (
            component(174288, 50),
            component(170457, 250),
            component(170000, 40),
            component(172336, 15),
        ),
        grams(305),
        "1 katori",
        grams(180),
    ),
    Recipe(
        "gujarati-ringna-bateta",
        "Ringna bateta nu shaak",
        "vegetable_dish",
        (
            component(169228, 150),
            component(170026, 150),
            component(170457, 80),
            component(172336, 15),
        ),
        grams(355),
        "1 katori",
        grams(180),
    ),
    Recipe(
        "gujarati-bhinda-nu-shaak",
        "Bhinda nu shaak",
        "vegetable_dish",
        (component(169260, 300), component(172336, 15), component(172231, 1)),
        grams(265),
        "1 katori",
        grams(160),
    ),
    Recipe(
        "gujarati-cabbage-sambharo",
        "Cabbage sambharo",
        "vegetable_dish",
        (
            component(169975, 250),
            component(170393, 100),
            component(172336, 10),
            component(170150, 3),
        ),
        grams(335),
        "1 katori",
        grams(150),
    ),
    Recipe(
        "gujarati-squash-dal",
        "Gujarati-style squash dal",
        "dal",
        (
            component(170487, 250),
            component(172436, 80),
            component(170457, 50),
            component(172336, 10),
        ),
        grams(405),
        "1 katori",
        grams(180),
    ),
    Recipe(
        "gujarati-moong-dal-chilla",
        "Moong dal chilla",
        "bread",
        (
            component(174256, 100),
            component(170000, 25),
            component(170457, 25),
            component(172336, 8),
        ),
        grams(235),
        "1 chilla",
        grams(59),
    ),
    Recipe(
        "gujarati-sukhdi",
        "Sukhdi (granulated-sugar model)",
        "sweet",
        (
            component(168893, 100),
            component(169655, 60),
            component(171314, 50),
        ),
        grams(210),
        "1 square",
        grams(35),
    ),
)

SEITAN_RECIPE = Recipe(
    "vegetarian-protein-plain-seitan",
    "Plain seitan, cooked-yield calculation",
    "protein",
    (component(168147, 100),),
    grams(280),
    "100 g weighed portion",
    grams(100),
    ("vegetarian", "vegan", "protein", "wheat"),
)

PACKS = {
    "gujarati-home-cooking": {
        "name": "Gujarati home cooking",
        "description": (
            "Gujarati dishes and everyday household staples backed by USDA records and "
            "auditable contributor-authored recipe calculations."
        ),
        "locale": "en-IN",
        "count": 50,
    },
    "indian-staples-north": {
        "name": "North Indian staples",
        "description": (
            "Common North Indian grains, breads, legumes, vegetables, dairy, and dishes "
            "selected from USDA FoodData Central."
        ),
        "locale": "en-IN",
        "count": 60,
    },
    "common-vegetarian-proteins": {
        "name": "Common vegetarian proteins",
        "description": (
            "Generic vegetarian protein foods including paneer, tofu, tempeh, legumes, "
            "nuts, seeds, eggs, dairy, and seitan."
        ),
        "locale": "en",
        "count": 30,
    },
    "supplements-and-powders": {
        "name": "Supplements and powders",
        "description": (
            "Unbranded protein powders, milks, brans, seeds, and other powdered foods "
            "with public-domain nutrient data."
        ),
        "locale": "en",
        "count": 25,
    },
}

CORE_NUTRIENTS = {
    1008: "energy_kcal",
    1003: "protein_g",
    1004: "fat_g",
    1005: "carbohydrate_g",
}
EXTRA_NUTRIENTS = {
    1079: "fiber_g",
    1093: "sodium_mg",
    1258: "saturated_fat_g",
    2000: "sugars_g",
}


def load_records(path: Path, dataset: Dataset, wanted: set[int]) -> dict[int, dict[str, Any]]:
    found: dict[int, dict[str, Any]] = {}
    with path.open("rb") as handle:
        for item in ijson.items(handle, f"{dataset.root}.item"):
            fdc_id = item.get("fdcId")
            if fdc_id in wanted:
                found[int(fdc_id)] = item
                if len(found) == len(wanted):
                    break
    missing = wanted - found.keys()
    if missing:
        raise ValueError(f"{dataset.label} is missing curated FDC IDs: {sorted(missing)}")
    return found


def nutrient_map(record: dict[str, Any], *, include_extra: bool = True) -> dict[str, Decimal]:
    wanted = dict(CORE_NUTRIENTS)
    if include_extra:
        wanted.update(EXTRA_NUTRIENTS)
    values: dict[str, Decimal] = {}
    for item in record.get("foodNutrients", []):
        nutrient_id = item.get("nutrient", {}).get("id")
        field = wanted.get(nutrient_id)
        amount = item.get("amount")
        if field is not None and amount is not None:
            values[field] = Decimal(str(amount))
    missing = set(CORE_NUTRIENTS.values()) - values.keys()
    if missing:
        raise ValueError(f"FDC {record.get('fdcId')} lacks core nutrients: {sorted(missing)}")
    return values


def rounded_nutrients(values: dict[str, Decimal]) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    for name, value in values.items():
        places = Decimal("0.1") if name == "energy_kcal" else Decimal("0.01")
        rounded = value.quantize(places)
        result[name] = int(rounded) if rounded == rounded.to_integral() else float(rounded)
    return result


def slugify(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


def category_for(description: str) -> str:
    lowered = description.lower()
    if any(word in lowered for word in ("dal", "bean", "pea", "lentil", "chickpea")):
        return "legume"
    if any(word in lowered for word in ("barfi", "dessert", "pudding", "sweet")):
        return "sweet"
    if any(word in lowered for word in ("milk", "yogurt", "cheese", "paneer")):
        return "dairy"
    if any(word in lowered for word in ("tofu", "tempeh")):
        return "legume"
    if any(word in lowered for word in ("rice", "wheat", "bread", "naan", "puri", "paratha")):
        return "grain"
    if any(word in lowered for word in ("powder", "flour", "bran", "whey", "gelatin")):
        return "powder"
    if any(word in lowered for word in ("seed", "nut", "almond", "cashew", "pistachio")):
        return "nuts_seeds"
    if any(word in lowered for word in ("curry", "samosa", "pudding", "upma", "dosa", "idli")):
        return "prepared_dish"
    return "vegetable"


def portion_name(portion: dict[str, Any]) -> str:
    description = str(portion.get("portionDescription") or "").strip()
    if description:
        return description
    amount = portion.get("amount", portion.get("value", 1))
    try:
        amount_text = format(Decimal(str(amount)).normalize(), "f")
    except (ArithmeticError, ValueError):
        amount_text = str(amount)
    unit = str(portion.get("measureUnit", {}).get("name") or "").strip()
    modifier = str(portion.get("modifier") or "").strip()
    pieces = [amount_text, unit, modifier]
    return " ".join(piece for piece in pieces if piece)


def fallback_portion(description: str, pack_id: str) -> tuple[str, Decimal]:
    lowered = description.lower()
    if pack_id == "supplements-and-powders":
        return "30 g weighed scoop", Decimal(30)
    if pack_id == "common-vegetarian-proteins":
        if "egg" in lowered:
            return "1 large egg", Decimal(50)
        if any(word in lowered for word in ("cottage", "yogurt", "paneer")):
            return "1/2 cup", Decimal(113)
        if any(word in lowered for word in ("tofu", "tempeh", "seitan")):
            return "3 oz portion", Decimal(85)
        if any(word in lowered for word in ("bean", "lentil", "soybean", "edamame")):
            return "1/2 cup", Decimal(90)
        if any(word in lowered for word in ("nut", "seed", "almond", "cashew", "pistachio")):
            return "1 oz portion", Decimal(28)
        if any(word in lowered for word in ("flour", "gluten")):
            return "1/4 cup", Decimal(30)
    return "100 g weighed portion", Decimal(100)


def choose_portion(
    record: dict[str, Any], pack_id: str
) -> tuple[dict[str, int | float | str], bool]:
    rejected = (
        "quantity not specified",
        "guideline amount",
        "surface inch",
        "cubic inch",
        "undetermined",
        "nfs",
    )
    candidates: list[tuple[int, str, Decimal]] = []
    for portion in record.get("foodPortions", []):
        name = portion_name(portion)
        weight = portion.get("gramWeight")
        if not name or weight is None or any(term in name.lower() for term in rejected):
            continue
        decimal_weight = Decimal(str(weight))
        if decimal_weight <= 0 or decimal_weight > 1000:
            continue
        lowered = name.lower()
        score = 0 if "cup" in lowered else 1 if any(x in lowered for x in ("piece", "item")) else 2
        candidates.append((score, name, decimal_weight))
    if candidates:
        _, name, weight = sorted(candidates, key=lambda item: (item[0], item[2], item[1]))[0]
        from_source = True
    else:
        name, weight = fallback_portion(str(record["description"]), pack_id)
        from_source = False
    rounded = weight.quantize(Decimal("0.01"))
    portion = {
        "name": name,
        "grams": int(rounded) if rounded == rounded.to_integral() else float(rounded),
    }
    return portion, from_source


def direct_entry(pack_id: str, source: SourceRef, record: dict[str, Any]) -> dict[str, Any]:
    description = str(record["description"]).strip()
    prefix = {
        "gujarati-home-cooking": "gujarati",
        "indian-staples-north": "north-indian",
        "common-vegetarian-proteins": "vegetarian-protein",
        "supplements-and-powders": "supplement",
    }[pack_id]
    tags = [prefix, "usda"]
    if pack_id != "indian-staples-north" or not any(
        word in description.lower() for word in ("chicken", "fish", "beef")
    ):
        tags.insert(0, "vegetarian")
    portion, portion_from_source = choose_portion(record, pack_id)
    category = category_for(description)
    if pack_id == "common-vegetarian-proteins":
        category = "protein"
    elif pack_id == "supplements-and-powders":
        category = "supplement"
    source_note = (
        f"USDA FoodData Central {source.dataset.label} bulk release "
        f"{source.dataset.release}, FDC ID {source.fdc_id}. Nutrient values are reproduced "
        "from the public-domain record."
    )
    source_note += (
        " The household portion weight is also from that record."
        if portion_from_source
        else " The named portion is a transparent opennosh logging estimate, not a USDA measure."
    )
    return {
        "slug": f"{prefix}-{slugify(description)}"[:120].rstrip("-"),
        "name": description,
        "category": category,
        "tags": tags,
        "contributed_by": CONTRIBUTOR,
        "provenance": "government_database",
        "source_uri": FDC_URI.format(fdc_id=source.fdc_id),
        "source_license": "CC0-1.0",
        "source_note": source_note,
        "basis": "per_100g",
        "nutrients": rounded_nutrients(nutrient_map(record)),
        "portions": [portion],
    }


def recipe_entry(recipe: Recipe, sr_records: dict[int, dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Decimal] = {name: Decimal(0) for name in CORE_NUTRIENTS.values()}
    formula: list[str] = []
    for item in recipe.components:
        profile = nutrient_map(sr_records[item.fdc_id], include_extra=False)
        for nutrient, amount in profile.items():
            totals[nutrient] += amount * item.grams / Decimal(100)
        formula.append(f"{INGREDIENTS[item.fdc_id]} (FDC {item.fdc_id}) {item.grams:g} g")
    per_100g = {
        nutrient: amount / recipe.yield_grams * Decimal(100) for nutrient, amount in totals.items()
    }
    return {
        "slug": recipe.slug,
        "name": recipe.name,
        "category": recipe.category,
        "tags": list(recipe.tags),
        "contributed_by": CONTRIBUTOR,
        "provenance": "published_recipe_calculation",
        "source_uri": None,
        "source_license": "contributor-original",
        "source_note": (
            "Contributor-authored standard recipe model calculated from USDA FoodData Central "
            f"SR Legacy ingredient profiles. Batch formula: {'; '.join(formula)}. Water and "
            f"seasonings without material energy are omitted. Cooked yield: {recipe.yield_grams:g} "
            "g. Values are per 100 g and rounded to two decimals; actual home recipes vary."
        ),
        "basis": "per_100g",
        "nutrients": rounded_nutrients(per_100g),
        "portions": [
            {
                "name": recipe.portion_name,
                "grams": int(recipe.portion_grams),
            }
        ],
    }


def core(entry: dict[str, Any]) -> tuple[Decimal, ...]:
    nutrients = entry["nutrients"]
    return tuple(Decimal(str(nutrients[name])) for name in CORE_NUTRIENTS.values())


def assert_quality(packs: dict[str, list[dict[str, Any]]]) -> None:
    seen_slugs: set[str] = set()
    profiles: list[tuple[str, tuple[Decimal, ...]]] = []
    for pack_id, entries in packs.items():
        expected = int(PACKS[pack_id]["count"])
        if len(entries) != expected:
            raise ValueError(f"{pack_id} has {len(entries)} entries; expected {expected}")
        for entry in entries:
            slug = entry["slug"]
            if slug in seen_slugs:
                raise ValueError(f"duplicate slug: {slug}")
            seen_slugs.add(slug)
            energy, protein, fat, carbohydrate = core(entry)
            calculated = protein * 4 + fat * 9 + carbohydrate * 4
            if energy == 0:
                mismatched = calculated != 0
            else:
                mismatched = abs(calculated - energy) / energy > Decimal("0.15")
            if mismatched:
                raise ValueError(
                    f"{slug}: macro energy {calculated} differs from source energy {energy}"
                )
            for previous_slug, previous in profiles:
                near = all(
                    abs(left - right) / max(abs(left), abs(right), Decimal(1)) <= Decimal("0.01")
                    for left, right in zip(core(entry), previous, strict=True)
                )
                if near:
                    raise ValueError(f"{slug} is nutritionally near-duplicate to {previous_slug}")
            profiles.append((slug, core(entry)))


def yaml_text(value: Any) -> str:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=100)


def pack_readme(pack_id: str, direct_count: int, recipe_count: int) -> str:
    info = PACKS[pack_id]
    method = (
        f"{direct_count} entries reproduce USDA FoodData Central nutrient profiles"
        if direct_count
        else "No entries directly reproduce a government record"
    )
    if recipe_count:
        method += (
            f"; {recipe_count} entries are contributor-authored batch calculations whose "
            "ingredient weights, FDC IDs, and cooked yields appear in each source note"
        )
    return f"""# {info["name"]}

{info["description"]}

## Data method

{method}. Values use a per-100-gram basis and every entry includes at least one named household
or mass-defined portion. USDA data is public domain (represented as `CC0-1.0` in the pack schema);
original calculation material is dedicated under CC0 1.0 with visible contributor credit.

This starter pack is a practical logging seed, not a clinical reference. Recipes, ingredients,
water loss, and brands vary. Prefer a product label, laboratory result, or private recipe when it
better represents the food actually eaten.
"""


def build(
    fndds_records: dict[int, dict[str, Any]], sr_records: dict[int, dict[str, Any]]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, tuple[int, int]]]:
    packs: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, tuple[int, int]] = {}

    gujarati_direct = [
        direct_entry("gujarati-home-cooking", SourceRef(FNDDS, fdc_id), fndds_records[fdc_id])
        for fdc_id in GUJARATI_DIRECT_IDS
    ]
    gujarati_recipes = [recipe_entry(recipe, sr_records) for recipe in GUJARATI_RECIPES]
    packs["gujarati-home-cooking"] = gujarati_direct + gujarati_recipes
    counts["gujarati-home-cooking"] = (len(gujarati_direct), len(gujarati_recipes))

    packs["indian-staples-north"] = [
        direct_entry("indian-staples-north", SourceRef(FNDDS, fdc_id), fndds_records[fdc_id])
        for fdc_id in NORTH_DIRECT_IDS
    ]
    counts["indian-staples-north"] = (len(NORTH_DIRECT_IDS), 0)

    protein_entries = [
        direct_entry("common-vegetarian-proteins", SourceRef(SR_LEGACY, fdc_id), sr_records[fdc_id])
        for fdc_id in PROTEIN_SR_IDS
    ]
    protein_entries.append(
        direct_entry(
            "common-vegetarian-proteins",
            SourceRef(FNDDS, 2705740),
            fndds_records[2705740],
        )
    )
    protein_entries.append(recipe_entry(SEITAN_RECIPE, sr_records))
    packs["common-vegetarian-proteins"] = protein_entries
    counts["common-vegetarian-proteins"] = (29, 1)

    packs["supplements-and-powders"] = [
        direct_entry("supplements-and-powders", SourceRef(SR_LEGACY, fdc_id), sr_records[fdc_id])
        for fdc_id in SUPPLEMENT_SR_IDS
    ]
    counts["supplements-and-powders"] = (len(SUPPLEMENT_SR_IDS), 0)

    assert_quality(packs)
    return packs, counts


def render_files(
    output: Path,
    packs: dict[str, list[dict[str, Any]]],
    counts: dict[str, tuple[int, int]],
) -> dict[Path, str]:
    rendered: dict[Path, str] = {}
    for pack_id, entries in packs.items():
        info = PACKS[pack_id]
        manifest = {
            "id": pack_id,
            "name": info["name"],
            "description": info["description"],
            "version": "1.0.0",
            "locale": info["locale"],
            "license": "CC0-1.0",
            "maintainers": [{"github": CONTRIBUTOR}],
            "entry_count": len(entries),
        }
        direct_count, recipe_count = counts[pack_id]
        rendered[output / pack_id / "pack.yaml"] = yaml_text(manifest)
        rendered[output / pack_id / "foods" / "foods.yaml"] = yaml_text(entries)
        rendered[output / pack_id / "README.md"] = pack_readme(pack_id, direct_count, recipe_count)
    return rendered


def write_or_check(rendered: dict[Path, str], *, check: bool) -> None:
    drift: list[Path] = []
    for path, content in rendered.items():
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                drift.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    if drift:
        raise SystemExit("Generated starter packs are stale: " + ", ".join(map(str, drift)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fndds-json", required=True, type=Path)
    parser.add_argument("--sr-legacy-json", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path("packs"))
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    fndds_ids = set(GUJARATI_DIRECT_IDS) | set(NORTH_DIRECT_IDS) | {2705740}
    sr_ids = set(PROTEIN_SR_IDS) | set(SUPPLEMENT_SR_IDS) | set(INGREDIENTS) | {168147}
    fndds_records = load_records(args.fndds_json, FNDDS, fndds_ids)
    sr_records = load_records(args.sr_legacy_json, SR_LEGACY, sr_ids)
    packs, counts = build(fndds_records, sr_records)
    write_or_check(render_files(args.output, packs, counts), check=args.check)
    print(f"Built {sum(len(entries) for entries in packs.values())} entries in {len(packs)} packs")


if __name__ == "__main__":
    main()
