from fastapi import APIRouter, Depends, Request

from visual_platform.api.auth import current_user
from visual_platform.domain.schemas import QuoteRequest
from visual_platform.services.quotes import create_quote

router = APIRouter(prefix="/api/v1/quotes", tags=["quotes"])


@router.post("", status_code=201)
async def quote(body: QuoteRequest, request: Request, user=Depends(current_user)):
    return await create_quote(
        request.app.state.api_pool,
        user["id"],
        body,
        live=request.app.state.settings.provider_profile == "bailian_live",
    )
