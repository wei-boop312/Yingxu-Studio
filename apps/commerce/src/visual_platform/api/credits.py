from fastapi import APIRouter, Depends, Query, Request

from visual_platform.api.auth import current_user
from visual_platform.api.products import cursor_decode, cursor_encode
from visual_platform.db.transactions import tenant_transaction

router = APIRouter(prefix="/api/v1/credits", tags=["credits"])


@router.get("/balance")
async def balance(request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        row = await c.fetchrow(
            "SELECT available,reserved,funded_total,spent_total FROM app.wallets WHERE user_id=$1",
            user["id"],
        )
    return dict(row) if row else dict(available=0, reserved=0, funded_total=0, spent_total=0)


@router.get("/transactions")
async def transactions(
    request: Request,
    user=Depends(current_user),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
):
    pos = cursor_decode(cursor) if cursor else None
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        rows = await c.fetch(
            "SELECT id,task_id,type,amount,available_after,reserved_after,reason,created_at FROM app.credit_transactions WHERE ($1::timestamptz IS NULL OR (created_at,id)<($1,$2::uuid)) ORDER BY created_at DESC,id DESC LIMIT $3",
            pos[0] if pos else None,
            pos[1] if pos else None,
            limit + 1,
        )
    return {
        "items": [dict(x) for x in rows[:limit]],
        "next_cursor": cursor_encode(rows[limit - 1]) if len(rows) > limit else None,
    }
