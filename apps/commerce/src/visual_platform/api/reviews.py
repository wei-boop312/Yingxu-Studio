import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from visual_platform.api.auth import current_user
from visual_platform.api.products import cursor_decode, cursor_encode
from visual_platform.db.transactions import tenant_transaction

router = APIRouter(prefix="/api/v1/tasks", tags=["reviews"])


@router.get("/{task_id}/compliance/reviews")
async def listing(
    task_id: UUID,
    request: Request,
    cursor: str | None = None,
    check_id: UUID | None = None,
    user=Depends(current_user),
):
    pos = cursor_decode(cursor) if cursor else None
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        check = await c.fetchval(
            "SELECT compliance_id FROM app.generation_tasks WHERE id=$1", task_id
        )
        if not check:
            raise HTTPException(404, "not_found")
        if check_id:
            check = await c.fetchval(
                "SELECT id FROM app.compliance_checks WHERE id=$1 AND task_id=$2", check_id, task_id
            )
            if not check:
                raise HTTPException(404, "not_found")
        rows = await c.fetch(
            "SELECT id,reviewer_id,decision,reason,evidence_refs,created_at FROM app.compliance_reviews WHERE check_id=$1 AND ($2::timestamptz IS NULL OR (created_at,id)<($2,$3::uuid)) ORDER BY created_at DESC,id DESC LIMIT 21",
            check,
            pos[0] if pos else None,
            pos[1] if pos else None,
        )
    return {
        "items": [
            {**dict(row), "evidence_refs": json.loads(row["evidence_refs"])} for row in rows[:20]
        ],
        "next_cursor": cursor_encode(rows[19]) if len(rows) > 20 else None,
    }
