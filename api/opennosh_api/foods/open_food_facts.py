from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.foods.schemas import (
    OpenFoodFactsAttribution,
    OpenFoodFactsExport,
    OpenFoodFactsExportEntry,
    OpenFoodFactsFood,
)
from opennosh_api.integrations.open_food_facts import OpenFoodFactsProduct
from opennosh_api.models import FoodOdbl

EXPORT_ROW_LIMIT = 10_000
EXPORT_MAX_SERIALIZED_BYTES = 64 * 1024 * 1024


class OpenFoodFactsExportLimitError(RuntimeError):
    """The isolated ODbL cache exceeded the bounded JSON export size."""


class OpenFoodFactsExportTimeoutError(RuntimeError):
    """PostgreSQL stopped the isolated ODbL export at its configured deadline."""


def food_response(row: FoodOdbl, *, cached: bool) -> OpenFoodFactsFood:
    return OpenFoodFactsFood(
        id=f"openfoodfacts:{row.barcode}",
        source_id=row.barcode,
        barcode=row.barcode,
        name=row.product_name,
        brand=row.brand,
        nutrients=row.nutrients_json,
        attribution=OpenFoodFactsAttribution(
            source_url=row.source_url,
            attribution_text=row.attribution_text,
        ),
        cached=cached,
    )


async def get_cached_product(database: AsyncSession, barcode: str) -> OpenFoodFactsFood | None:
    row = await database.scalar(select(FoodOdbl).where(FoodOdbl.barcode == barcode))
    return food_response(row, cached=True) if row is not None else None


async def cache_product(
    database: AsyncSession, product: OpenFoodFactsProduct
) -> OpenFoodFactsFood:
    statement = (
        insert(FoodOdbl)
        .values(
            barcode=product.barcode,
            product_name=product.product_name,
            brand=product.brand,
            nutrients_json=product.nutrients_json,
            source_url=product.source_url,
            attribution_text=product.attribution_text,
        )
        .on_conflict_do_nothing(index_elements=[FoodOdbl.barcode])
        .returning(FoodOdbl.id)
    )
    inserted_id = (await database.execute(statement)).scalar_one_or_none()
    await database.commit()
    row = await database.scalar(select(FoodOdbl).where(FoodOdbl.barcode == product.barcode))
    if row is None:  # pragma: no cover - the insert/select invariant is database-enforced
        raise RuntimeError("Open Food Facts cache insert did not produce a row")
    return food_response(row, cached=inserted_id is None)


def _export_entry(row: FoodOdbl) -> OpenFoodFactsExportEntry:
    return OpenFoodFactsExportEntry(
        barcode=row.barcode,
        product_name=row.product_name,
        brand=row.brand,
        nutrients=row.nutrients_json,
        source_url=row.source_url,
        attribution_text=row.attribution_text,
    )


async def export_cached_products(
    database: AsyncSession, *, statement_timeout_ms: int
) -> OpenFoodFactsExport:
    await database.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": f"{statement_timeout_ms}ms"},
    )
    try:
        rows = await database.stream_scalars(
            select(FoodOdbl)
            .order_by(FoodOdbl.barcode)
            .limit(EXPORT_ROW_LIMIT + 1)
            .execution_options(yield_per=100)
        )
        entries: list[OpenFoodFactsExportEntry] = []
        serialized_bytes = len(OpenFoodFactsExport(entries=[]).model_dump_json().encode())
        try:
            async for row in rows:
                if len(entries) == EXPORT_ROW_LIMIT:
                    raise OpenFoodFactsExportLimitError
                entry = _export_entry(row)
                serialized_bytes += len(entry.model_dump_json().encode())
                if entries:
                    serialized_bytes += 1
                if serialized_bytes > EXPORT_MAX_SERIALIZED_BYTES:
                    raise OpenFoodFactsExportLimitError
                entries.append(entry)
        finally:
            await rows.close()
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != "57014":
            raise
        await database.rollback()
        raise OpenFoodFactsExportTimeoutError from error
    return OpenFoodFactsExport(entries=entries)
