"""Explicit, billable product understanding with durable quote/intent and tenant isolation."""

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import Field

from visual_platform.api.auth import current_user
from visual_platform.api.products import AppearanceCreate
from visual_platform.core.credentials import private_credential
from visual_platform.db.transactions import tenant_transaction
from visual_platform.domain.schemas import StrictModel
from visual_platform.providers.appearance_understanding import parse_summary, understand
from visual_platform.providers.contract import ProviderFailure
from visual_platform.providers.runtime import credential_fingerprint, load_runtime

router = APIRouter(prefix="/api/v1/appearance-understandings", tags=["appearance-understanding"])


class UnderstandingQuote(StrictModel):
    product_id: UUID
    source_asset_version_id: UUID


class ConfirmAppearance(AppearanceCreate):
    confirmed: bool = Field(strict=True)


async def configuration(request, c):
    if request.app.state.settings.provider_profile != "bailian_live":
        raise HTTPException(409, "appearance_live_provider_required")
    try:
        config = json.loads(await c.fetchval("SELECT app.live_provider_configuration()"))
        runtime = load_runtime(Path(os.environ["LIVE_PROVIDER_RUNTIME_FILE"]))
        key = private_credential("DASHSCOPE_API_KEY")
        if (
            runtime.schema_version != 3
            or runtime.digest() != config["binding"]["provider_runtime_sha256"]
            or not hmac.compare_digest(
                credential_fingerprint(key), runtime.detect.credential_fingerprint
            )
        ):
            raise ValueError("runtime_mismatch")
        directory = Path(os.environ["LIVE_PROVIDER_PRIVATE_DIR"]) / "appearance-understanding"
        return config, runtime, key, directory
    except (asyncpg.RaiseError, KeyError, ValueError, OSError):
        raise HTTPException(409, "appearance_provider_unavailable") from None


def public(row):
    result = dict(row)
    for name in ("result", "metering"):
        if isinstance(result.get(name), str):
            result[name] = json.loads(result[name])
    result.pop("user_id", None)
    result["actual_cost_cny"] = None
    result["actual_spending_verified"] = False
    return result


@router.post("/quotes", status_code=201)
async def quote(body: UnderstandingQuote, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        config, runtime, _, _ = await configuration(request, c)
        source = await c.fetchrow(
            "SELECT v.sha256,v.width,v.height,v.byte_size FROM app.asset_versions v JOIN app.assets a ON a.id=v.asset_id JOIN app.products p ON p.id=v.product_id WHERE v.id=$1 AND v.product_id=$2 AND v.canonical AND a.kind='original' AND a.status='ready' AND p.archived_at IS NULL",
            body.source_asset_version_id,
            body.product_id,
        )
        if not source:
            raise HTTPException(404, "source_unavailable")
        if (
            source["width"] * source["height"] > 4_000_000
            or source["byte_size"] > 10_000_000
            or max(source["width"], source["height"]) > 8000
            or max(source["width"], source["height"]) > 10 * min(source["width"], source["height"])
        ):
            raise HTTPException(422, "appearance_source_too_large")
        # A single unresolved submission for this source blocks another paid intent.
        await c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
            str(user["id"]) + str(body.source_asset_version_id),
        )
        if await c.fetchval(
            "SELECT 1 FROM app.appearance_understandings WHERE source_asset_version_id=$1 AND status IN ('submitting','uncertain')",
            body.source_asset_version_id,
        ):
            raise HTTPException(409, "appearance_reconciliation_required")
        row = await c.fetchrow(
            "INSERT INTO app.appearance_understandings(id,user_id,product_id,source_asset_version_id,source_sha256,runtime_sha256) VALUES($1,$2,$3,$4,$5,$6) RETURNING *",
            uuid4(),
            user["id"],
            body.product_id,
            body.source_asset_version_id,
            source["sha256"],
            runtime.digest(),
        )
    return {
        **public(row),
        "model": runtime.detect.model,
        "provider_profile": "bailian_live",
        "reference_rates_per_million_tokens": config["reference_rates"]["detect"],
        "platform_credits": 0,
        "billing_notice": "独立图像理解请求，按供应商实际Token计费；不会生成图片。实际金额待账单核实。",
    }


@router.get("")
async def listing(
    request: Request, product_id: UUID, source_asset_version_id: UUID, user=Depends(current_user)
):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        rows = await c.fetch(
            "SELECT * FROM app.appearance_understandings WHERE product_id=$1 AND source_asset_version_id=$2 AND status<>'quoted' ORDER BY (status IN ('submitting','uncertain')) DESC,created_at DESC LIMIT 10",
            product_id,
            source_asset_version_id,
        )
    return {"items": [public(row) for row in rows]}


@router.get("/{identity}")
async def detail(identity: UUID, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        row = await c.fetchrow("SELECT * FROM app.appearance_understandings WHERE id=$1", identity)
    if not row:
        raise HTTPException(404, "not_found")
    return public(row)


@router.post("/{identity}/submit")
async def submit(
    identity: UUID,
    request: Request,
    x_provider_confirmation: str = Header(default=""),
    user=Depends(current_user),
):
    if x_provider_confirmation != "bailian_live":
        raise HTTPException(409, "live_provider_confirmation_required")
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        row = await c.fetchrow(
            "SELECT * FROM app.appearance_understandings WHERE id=$1 FOR UPDATE", identity
        )
        if not row:
            raise HTTPException(404, "not_found")
        if row["status"] != "quoted":
            return public(row)  # Repeat requests only read existing state; never POST again.
        config, runtime, key, directory = await configuration(request, c)
        if runtime.digest() != row["runtime_sha256"] or await c.fetchval(
            "SELECT $1::timestamptz < now()", row["expires_at"]
        ):
            raise HTTPException(409, "quote_expired")
        await c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
            str(user["id"]) + str(row["source_asset_version_id"]),
        )
        if await c.fetchval(
            "SELECT 1 FROM app.appearance_understandings WHERE source_asset_version_id=$1 AND id<>$2 AND status IN ('submitting','uncertain')",
            row["source_asset_version_id"],
            identity,
        ):
            raise HTTPException(409, "appearance_reconciliation_required")
        source = await c.fetchrow(
            "SELECT v.* FROM app.asset_versions v JOIN app.assets a ON a.id=v.asset_id JOIN app.products p ON p.id=v.product_id WHERE v.id=$1 AND a.status='ready' AND p.archived_at IS NULL",
            row["source_asset_version_id"],
        )
        if not source or source["sha256"] != row["source_sha256"]:
            raise HTTPException(404, "source_unavailable")
        await c.execute(
            "UPDATE app.appearance_understandings SET status='submitting' WHERE id=$1", identity
        )
    # Committed before network. Process failure leaves an unresolved intent, not a retry.
    status = "uncertain"
    result = None
    digest = None
    metering = None
    error: str | None = "appearance_response_unknown"
    try:
        storage = request.app.state.storage
        if source["bucket"] != storage.bucket:
            raise ValueError("source_bucket_mismatch")
        image, _, _ = await asyncio.to_thread(
            storage.get,
            source["object_key"],
            version=source["storage_version_id"],
            limit=20 * 1024 * 1024,
        )
        if hashlib.sha256(image).hexdigest() != row["source_sha256"]:
            raise ValueError("source_hash_mismatch")
        summary, digest, metering = await understand(
            identity,
            image,
            key=key,
            origin=runtime.detect.endpoint_origin,
            directory=directory,
            live=True,
        )
        status, result, error = "succeeded", summary.model_dump(), None
    except ProviderFailure:
        status, error = "failed", "appearance_explicit_rejection"
    except Exception:
        pass  # No raw exception, key, signed URL or image content enters HTTP logs.
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        row = await c.fetchrow(
            "UPDATE app.appearance_understandings SET status=$2,result=$3::jsonb,response_sha256=$4,metering=$5::jsonb,error_code=$6 WHERE id=$1 AND status='submitting' RETURNING *",
            identity,
            status,
            json.dumps(result) if result else None,
            digest,
            json.dumps(metering) if metering else None,
            error,
        )
    return public(row)


@router.post("/{identity}/confirm", status_code=201)
async def confirm(
    identity: UUID, body: ConfirmAppearance, request: Request, user=Depends(current_user)
):
    if not body.confirmed or not body.config.description.strip():
        raise HTTPException(422, "appearance_confirmation_required")
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        row = await c.fetchrow(
            "SELECT * FROM app.appearance_understandings WHERE id=$1 FOR UPDATE", identity
        )
        if not row:
            raise HTTPException(404, "not_found")
        if row["status"] != "succeeded":
            raise HTTPException(409, "appearance_not_ready")
        if not await c.fetchval(
            "SELECT 1 FROM app.products WHERE id=$1 AND archived_at IS NULL FOR SHARE",
            row["product_id"],
        ):
            raise HTTPException(404, "product_unavailable")
        if row["confirmed_profile_id"]:
            profile = await c.fetchrow(
                "SELECT id,name,config,revision FROM app.appearance_profiles WHERE id=$1",
                row["confirmed_profile_id"],
            )
            previous = json.loads(profile["config"])
            for field in ("source_asset_version_id", "understanding_id"):
                previous.pop(field, None)
            submitted = body.config.model_dump(
                exclude={"source_asset_version_id", "understanding_id"}
            )
            if profile["name"] != body.name or previous != submitted:
                raise HTTPException(409, "idempotency_conflict")
        else:
            config = body.config.model_copy(
                update={
                    "source_asset_version_id": row["source_asset_version_id"],
                    "understanding_id": identity,
                }
            )
            profile = await c.fetchrow(
                "INSERT INTO app.appearance_profiles(id,user_id,product_id,name,config) VALUES($1,$2,$3,$4,$5::jsonb) RETURNING id,name,config,revision",
                uuid4(),
                user["id"],
                row["product_id"],
                body.name,
                config.model_dump_json(),
            )
            await c.execute(
                "UPDATE app.appearance_understandings SET confirmed_profile_id=$2 WHERE id=$1",
                identity,
                profile["id"],
            )
    return {**dict(profile), "config": json.loads(profile["config"])}


@router.post("/{identity}/recover")
async def recover(identity: UUID, request: Request, user=Depends(current_user)):
    """Recover only an already archived original reply. Never calls a supplier."""
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        row = await c.fetchrow(
            "SELECT * FROM app.appearance_understandings WHERE id=$1 FOR UPDATE", identity
        )
        if not row:
            raise HTTPException(404, "not_found")
        if row["status"] not in ("submitting", "uncertain"):
            return public(row)
        try:
            directory = Path(os.environ["LIVE_PROVIDER_PRIVATE_DIR"]) / "appearance-understanding"
            path = directory / (str(identity) + ".response.body")
            meta = directory / (str(identity) + ".response-metadata.json")
            if (
                directory.is_symlink()
                or directory.is_junction()
                or any(p.is_symlink() or not p.is_file() for p in (path, meta))
                or path.stat().st_size > 65536
                or meta.stat().st_size > 1024
            ):
                raise ValueError("original_reply_unavailable")
            with meta.open("rb") as stream:
                metadata = stream.read(1025)
            with path.open("rb") as stream:
                raw = stream.read(65537)
            if len(metadata) > 1024 or len(raw) > 65536:
                raise ValueError("reply_exceeds_limit")
            if json.loads(metadata) != {"http_status": 200}:
                raise ValueError("unconfirmed_http_status")
            summary, digest, metering = parse_summary(raw)
        except (KeyError, ValueError, TypeError, IndexError, AttributeError, OSError):
            return public(row)
        row = await c.fetchrow(
            "UPDATE app.appearance_understandings SET status='succeeded',result=$2::jsonb,response_sha256=$3,metering=$4::jsonb,error_code=NULL WHERE id=$1 RETURNING *",
            identity,
            summary.model_dump_json(),
            digest,
            json.dumps(metering),
        )
    return public(row)
