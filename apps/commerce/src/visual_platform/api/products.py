import base64
import json
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import Field

from visual_platform.api.auth import current_user
from visual_platform.db.transactions import tenant_transaction
from visual_platform.domain.appearance import AppearanceConfig
from visual_platform.domain.schemas import StrictModel

router = APIRouter(prefix="/api/v1/products", tags=["products"])


class ProductCreate(StrictModel):
    name: str = Field(strict=True, min_length=1, max_length=255)
    category: str = Field(strict=True, min_length=1, max_length=255)


class ProductPatch(StrictModel):
    name: str | None = Field(default=None, strict=True, min_length=1, max_length=255)
    category: str | None = Field(default=None, strict=True, min_length=1, max_length=255)


class AppearanceCreate(StrictModel):
    name: str = Field(strict=True, min_length=1, max_length=255)
    config: AppearanceConfig


def cursor_encode(row):
    return base64.urlsafe_b64encode(
        json.dumps([row["created_at"].isoformat(), str(row["id"])]).encode()
    ).decode()


def cursor_decode(cursor):
    try:
        if len(cursor) > 512:
            raise ValueError()
        timestamp, identity = json.loads(base64.urlsafe_b64decode(cursor))
        dt = datetime.fromisoformat(timestamp)
        if dt.tzinfo is None:
            raise ValueError()
        return dt, UUID(identity)
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, "invalid_cursor")


@router.post("", status_code=201)
async def create(body: ProductCreate, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        row = await c.fetchrow(
            "INSERT INTO app.products(id,user_id,name,category) VALUES($1,$2,$3,$4) RETURNING id,name,category,revision,archived_at,created_at,updated_at",
            uuid4(),
            user["id"],
            body.name,
            body.category,
        )
    return dict(row)


@router.get("")
async def listing(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
    user=Depends(current_user),
):
    pos = cursor_decode(cursor) if cursor else None
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        rows = await c.fetch(
            """SELECT id,name,category,revision,archived_at,created_at,updated_at FROM app.products
          WHERE ($1::timestamptz IS NULL OR (created_at,id)<($1,$2::uuid)) ORDER BY created_at DESC,id DESC LIMIT $3""",
            pos[0] if pos else None,
            pos[1] if pos else None,
            limit + 1,
        )
    return {
        "items": [dict(x) for x in rows[:limit]],
        "next_cursor": cursor_encode(rows[limit - 1]) if len(rows) > limit else None,
    }


@router.get("/{product_id}")
async def detail(product_id: UUID, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        row = await c.fetchrow(
            "SELECT id,name,category,revision,archived_at,created_at,updated_at FROM app.products WHERE id=$1",
            product_id,
        )
    if not row:
        raise HTTPException(404, "not_found")
    return dict(row)


@router.patch("/{product_id}")
async def update(
    product_id: UUID,
    body: ProductPatch,
    request: Request,
    if_match: str = Header(),
    user=Depends(current_user),
):
    try:
        revision = int(if_match.strip('"'))
    except ValueError:
        raise HTTPException(422, "invalid_revision")
    if not body.model_fields_set or any(getattr(body, k) is None for k in body.model_fields_set):
        raise HTTPException(422, "empty_or_null_patch")
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        existing = await c.fetchrow(
            "SELECT revision,archived_at FROM app.products WHERE id=$1 FOR UPDATE", product_id
        )
        if not existing:
            raise HTTPException(404, "not_found")
        if existing["revision"] != revision or existing["archived_at"]:
            raise HTTPException(409, "revision_conflict")
        row = await c.fetchrow(
            "UPDATE app.products SET name=COALESCE($2,name),category=COALESCE($3,category),revision=revision+1,updated_at=now() WHERE id=$1 RETURNING id,name,category,revision,archived_at",
            product_id,
            body.name,
            body.category,
        )
    return dict(row)


@router.delete("/{product_id}", status_code=204)
async def archive(product_id: UUID, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        row = await c.fetchrow(
            "UPDATE app.products SET archived_at=COALESCE(archived_at,now()),revision=revision+1,updated_at=now() WHERE id=$1 RETURNING id",
            product_id,
        )
        if not row:
            raise HTTPException(404, "not_found")


@router.post("/{product_id}/appearances", status_code=201)
async def appearance_create(
    product_id: UUID, body: AppearanceCreate, request: Request, user=Depends(current_user)
):
    if body.config.understanding_id or body.config.source_asset_version_id:
        raise HTTPException(422, "appearance_confirmation_required")
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        product = await c.fetchval(
            "SELECT id FROM app.products WHERE id=$1 AND archived_at IS NULL FOR SHARE", product_id
        )
        if not product:
            raise HTTPException(404, "not_found")
        row = await c.fetchrow(
            "INSERT INTO app.appearance_profiles(id,user_id,product_id,name,config) VALUES($1,$2,$3,$4,$5::jsonb) RETURNING id,name,config,revision",
            uuid4(),
            user["id"],
            product_id,
            body.name,
            body.config.model_dump_json(exclude_none=True),
        )
    result = dict(row)
    result["config"] = json.loads(result["config"])
    return result


@router.get("/{product_id}/appearances")
async def appearances(
    product_id: UUID,
    request: Request,
    user=Depends(current_user),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
    include_archived: bool = False,
):
    pos = cursor_decode(cursor) if cursor else None
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        if not await c.fetchval("SELECT id FROM app.products WHERE id=$1", product_id):
            raise HTTPException(404, "not_found")
        rows = await c.fetch(
            "SELECT id,name,config,revision,archived_at,created_at FROM app.appearance_profiles WHERE product_id=$1 AND ($5 OR archived_at IS NULL) AND ($2::timestamptz IS NULL OR (created_at,id)<($2,$3::uuid)) ORDER BY created_at DESC,id DESC LIMIT $4",
            product_id,
            pos[0] if pos else None,
            pos[1] if pos else None,
            limit + 1,
            include_archived,
        )
    return {
        "items": [{**dict(x), "config": json.loads(x["config"])} for x in rows[:limit]],
        "next_cursor": cursor_encode(rows[limit - 1]) if len(rows) > limit else None,
    }


@router.patch("/{product_id}/appearances/{appearance_id}")
async def appearance_update(
    product_id: UUID,
    appearance_id: UUID,
    body: AppearanceCreate,
    request: Request,
    if_match: str = Header(),
    user=Depends(current_user),
):
    try:
        revision = int(if_match.strip('"'))
    except ValueError:
        raise HTTPException(422, "invalid_revision")
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        if not await c.fetchval(
            "SELECT id FROM app.products WHERE id=$1 AND archived_at IS NULL FOR SHARE", product_id
        ):
            raise HTTPException(404, "not_found")
        old = await c.fetchrow(
            "SELECT revision,config FROM app.appearance_profiles WHERE id=$1 AND product_id=$2 AND archived_at IS NULL FOR UPDATE",
            appearance_id,
            product_id,
        )
        if not old:
            raise HTTPException(404, "not_found")
        if old["revision"] != revision:
            raise HTTPException(409, "revision_conflict")
        previous = json.loads(old["config"])
        for key in ("understanding_id", "source_asset_version_id"):
            value = getattr(body.config, key)
            if (str(value) if value else None) != previous.get(key):
                raise HTTPException(422, "appearance_source_binding_immutable")
        row = await c.fetchrow(
            "UPDATE app.appearance_profiles SET name=$2,config=$3::jsonb,revision=revision+1,updated_at=now() WHERE id=$1 RETURNING id,name,config,revision",
            appearance_id,
            body.name,
            body.config.model_dump_json(exclude_none=True),
        )
    return {**dict(row), "config": json.loads(row["config"])}


@router.get("/{product_id}/appearances/{appearance_id}")
async def appearance_detail(
    product_id: UUID, appearance_id: UUID, request: Request, user=Depends(current_user)
):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        row = await c.fetchrow(
            "SELECT id,name,config,revision,archived_at,created_at,updated_at FROM app.appearance_profiles WHERE id=$1 AND product_id=$2",
            appearance_id,
            product_id,
        )
    if not row:
        raise HTTPException(404, "not_found")
    return {**dict(row), "config": json.loads(row["config"])}


@router.delete("/{product_id}/appearances/{appearance_id}", status_code=204)
async def appearance_archive(
    product_id: UUID,
    appearance_id: UUID,
    request: Request,
    if_match: str = Header(),
    user=Depends(current_user),
):
    try:
        revision = int(if_match.strip('"'))
    except ValueError:
        raise HTTPException(422, "invalid_revision")
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        if not await c.fetchval(
            "SELECT id FROM app.products WHERE id=$1 AND archived_at IS NULL FOR SHARE", product_id
        ):
            raise HTTPException(404, "not_found")
        old = await c.fetchrow(
            "SELECT revision,archived_at FROM app.appearance_profiles WHERE id=$1 AND product_id=$2 FOR UPDATE",
            appearance_id,
            product_id,
        )
        if not old:
            raise HTTPException(404, "not_found")
        if old["archived_at"] is not None:
            return
        if old["revision"] != revision:
            raise HTTPException(409, "revision_conflict")
        await c.execute(
            "UPDATE app.appearance_profiles SET archived_at=now(),revision=revision+1,updated_at=now() WHERE id=$1",
            appearance_id,
        )
