from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import CurrentSession, get_current_session, require_csrf
from opennosh_api.database import get_database_session
from opennosh_api.recipes.schemas import RecipeListResponse, RecipeResponse, RecipeWrite
from opennosh_api.recipes.service import (
    RECIPE_LIST_LIMIT_DEFAULT,
    RECIPE_LIST_LIMIT_MAX,
    RECIPE_LIST_OFFSET_MAX,
    RecipeInputError,
    RecipeSourceNotFound,
    create_recipe,
    delete_recipe,
    get_recipe,
    list_recipes,
    update_recipe,
)

router = APIRouter(prefix="/api/v1/recipes", tags=["recipes"])


def _unprocessable(error: RecipeInputError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(error),
    )


@router.post("", response_model=RecipeResponse, status_code=status.HTTP_201_CREATED)
async def create(
    payload: RecipeWrite,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> RecipeResponse:
    try:
        return await create_recipe(database, payload, current)
    except RecipeSourceNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except RecipeInputError as error:
        raise _unprocessable(error) from error


@router.get("", response_model=RecipeListResponse)
async def list_all(
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
    limit: Annotated[int, Query(ge=1, le=RECIPE_LIST_LIMIT_MAX)] = RECIPE_LIST_LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0, le=RECIPE_LIST_OFFSET_MAX)] = 0,
) -> RecipeListResponse:
    return await list_recipes(
        database,
        current=current,
        limit=limit,
        offset=offset,
    )


@router.get("/{recipe_id}", response_model=RecipeResponse)
async def detail(
    recipe_id: UUID,
    current: Annotated[CurrentSession, Depends(get_current_session)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> RecipeResponse:
    recipe = await get_recipe(database, recipe_id, current)
    if recipe is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")
    return recipe


@router.put("/{recipe_id}", response_model=RecipeResponse)
async def update(
    recipe_id: UUID,
    payload: RecipeWrite,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> RecipeResponse:
    try:
        recipe = await update_recipe(database, recipe_id, payload, current)
    except RecipeSourceNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except RecipeInputError as error:
        raise _unprocessable(error) from error
    if recipe is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")
    return recipe


@router.delete("/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    recipe_id: UUID,
    current: Annotated[CurrentSession, Depends(require_csrf)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> None:
    if not await delete_recipe(database, recipe_id, current):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")
