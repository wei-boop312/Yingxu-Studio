import json

from fastapi import APIRouter, Depends, HTTPException, Request

from visual_platform.api.auth import current_user
from visual_platform.db.transactions import tenant_transaction
from visual_platform.domain.platforms import DEFAULT_MARKETPLACES, PLATFORMS

router = APIRouter(prefix="/api/v1/rules", tags=["rules"])


@router.get("/platforms")
async def platforms(user=Depends(current_user)):
    return {
        "items": [{"platform": p, "default_evidence_status": "unverified"} for p in PLATFORMS],
        "next_cursor": None,
    }


@router.get("/{platform}")
async def rules(
    platform: str,
    request: Request,
    marketplace: str | None = None,
    category: str = "generic",
    image_type: str = "MAIN",
    user=Depends(current_user),
):
    if platform not in PLATFORMS:
        raise HTTPException(404, "not_found")
    marketplace = marketplace or DEFAULT_MARKETPLACES[platform]
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        row = await c.fetchrow(
            "SELECT id,version,evidence_status,source_urls,retrieved_at,evidence_sha256,config_sha256,published_at,config FROM app.platform_rules WHERE platform=$1 AND marketplace=$2 AND category=$3 AND image_type=$4 AND published_at IS NOT NULL ORDER BY published_at DESC,id DESC LIMIT 1",
            platform,
            marketplace,
            category,
            image_type,
        )
    if not row:
        return dict(
            platform=platform,
            marketplace=marketplace,
            category=category,
            image_type=image_type,
            version="draft-20261112",
            evidence_status="unverified",
            source_urls=[],
            config={},
        )
    return {
        **dict(row),
        "platform": platform,
        "marketplace": marketplace,
        "category": category,
        "image_type": image_type,
        "source_urls": json.loads(row["source_urls"]),
        "config": json.loads(row["config"]),
    }
