import asyncio
import json
from typing import Literal
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import Field

from visual_platform.api.auth import current_user
from visual_platform.api.products import cursor_decode, cursor_encode
from visual_platform.db.transactions import tenant_transaction
from visual_platform.domain.schemas import StrictModel
from visual_platform.services.quotes import db_conflict
from visual_platform.services.upload import complete_upload

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])


class Upload(StrictModel):
    product_id: UUID
    filename: str = Field(strict=True, min_length=1, max_length=255)
    mime: Literal["image/jpeg", "image/png", "image/webp"]
    size: int = Field(strict=True, ge=1, le=20 * 1024 * 1024)


@router.post("/upload-intents", status_code=201)
async def create(body: Upload, request: Request, user=Depends(current_user)):
    storage = request.app.state.storage
    if storage is None:
        raise HTTPException(503, "storage_not_configured")
    identity = uuid4()
    asset = uuid4()
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        try:
            row = json.loads(
                await c.fetchval(
                    "SELECT app.create_upload($1,$2,$3,$4,$5)",
                    identity,
                    asset,
                    body.product_id,
                    body.mime,
                    body.size,
                )
            )
        except asyncpg.RaiseError as exc:
            db_conflict(exc)
        expiry = await c.fetchval("SELECT expires_at FROM app.upload_intents WHERE id=$1", identity)
    url = await asyncio.to_thread(storage.upload_url, row["temp_key"], body.mime, body.size)
    return {
        "intent_id": identity,
        "asset_id": asset,
        "upload_url": url,
        "headers": {"Content-Type": body.mime},
        "expires_at": expiry,
    }


async def version_info(pool, uid, identity):
    async with tenant_transaction(pool, uid) as c:
        row = await c.fetchrow(
            "SELECT id AS version_id,asset_id,mime,width,height,sha256,byte_size,canonical FROM app.asset_versions WHERE id=$1",
            identity,
        )
    if not row:
        raise HTTPException(404, "not_found")
    return dict(row)


@router.post("/upload-intents/{intent_id}/complete")
async def complete(intent_id: UUID, request: Request, user=Depends(current_user)):
    identity = await complete_upload(request.app, user["id"], intent_id)
    return {
        "status": "ready",
        **await version_info(request.app.state.api_pool, user["id"], identity),
    }


@router.get("/{asset_id}")
async def detail(asset_id: UUID, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        asset = await c.fetchrow(
            "SELECT id,product_id,kind,status,current_version_id FROM app.assets WHERE id=$1",
            asset_id,
        )
    if not asset:
        raise HTTPException(404, "not_found")
    return {
        **dict(asset),
        "version": await version_info(
            request.app.state.api_pool, user["id"], asset["current_version_id"]
        )
        if asset["current_version_id"]
        else None,
    }


@router.get("/{asset_id}/download")
async def download(
    asset_id: UUID, request: Request, version_id: UUID | None = None, user=Depends(current_user)
):
    storage = request.app.state.storage
    if storage is None:
        raise HTTPException(503, "storage_not_configured")
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        row = await c.fetchrow(
            "SELECT v.bucket,v.object_key,v.storage_version_id,v.mime FROM app.assets a JOIN app.asset_versions v ON v.asset_id=a.id AND v.user_id=a.user_id WHERE a.id=$1 AND v.id=COALESCE($2,a.current_version_id)",
            asset_id,
            version_id,
        )
    if not row:
        raise HTTPException(404, "not_found")
    if row["bucket"] != storage.bucket:
        raise HTTPException(503, "storage_bucket_unavailable")
    url = await asyncio.to_thread(
        storage.download_url,
        row["object_key"],
        row["storage_version_id"],
        "image.png" if row["mime"] == "image/png" else "original",
    )
    return RedirectResponse(url, status_code=302)


@router.get("/{asset_id}/versions")
async def versions(
    asset_id: UUID, request: Request, cursor: str | None = None, user=Depends(current_user)
):
    pos = cursor_decode(cursor) if cursor else None
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        if not await c.fetchval("SELECT id FROM app.assets WHERE id=$1", asset_id):
            raise HTTPException(404, "not_found")
        rows = await c.fetch(
            "SELECT id,version_number,mime,width,height,sha256,byte_size,canonical,created_at FROM app.asset_versions WHERE asset_id=$1 AND ($2::timestamptz IS NULL OR (created_at,id)<($2,$3::uuid)) ORDER BY created_at DESC,id DESC LIMIT 21",
            asset_id,
            pos[0] if pos else None,
            pos[1] if pos else None,
        )
    return {
        "items": [dict(x) for x in rows[:20]],
        "next_cursor": cursor_encode(rows[19]) if len(rows) > 20 else None,
    }


@router.get("")
async def listing(
    request: Request, product_id: UUID, cursor: str | None = None, user=Depends(current_user)
):
    pos = cursor_decode(cursor) if cursor else None
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        if not await c.fetchval("SELECT id FROM app.products WHERE id=$1", product_id):
            raise HTTPException(404, "not_found")
        rows = await c.fetch(
            "SELECT a.id,a.kind,a.status,a.created_at,v.id AS version_id,v.width,v.height,v.sha256 FROM app.assets a LEFT JOIN app.asset_versions v ON v.id=a.current_version_id AND v.user_id=a.user_id WHERE a.product_id=$1 AND a.kind='original' AND ($2::timestamptz IS NULL OR (a.created_at,a.id)<($2,$3::uuid)) ORDER BY a.created_at DESC,a.id DESC LIMIT 21",
            product_id,
            pos[0] if pos else None,
            pos[1] if pos else None,
        )
    return {
        "items": [dict(x) for x in rows[:20]],
        "next_cursor": cursor_encode(rows[19]) if len(rows) > 20 else None,
    }
