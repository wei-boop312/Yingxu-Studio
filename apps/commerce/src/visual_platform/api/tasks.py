import json
import logging
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from visual_platform.api.auth import current_user
from visual_platform.api.products import cursor_decode, cursor_encode
from visual_platform.core.event_replay import requires_event_reset
from visual_platform.db.transactions import tenant_transaction
from visual_platform.domain.schemas import QuoteRequest, TaskRequest, canonical_hash
from visual_platform.services.quotes import db_conflict, hash_data

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

COLUMNS = "product_id,source_asset_version_id,(SELECT asset_id FROM app.asset_versions WHERE id=generation_tasks.source_asset_version_id) AS source_asset_id,id,status,phase,revision,credits_status,reserved_credits,created_at,updated_at,completed_at,error_code,final_version_id,compliance_id,(SELECT sha256 FROM app.asset_versions WHERE id=generation_tasks.final_version_id) AS final_sha256,(SELECT width FROM app.asset_versions WHERE id=generation_tasks.final_version_id) AS final_width,(SELECT height FROM app.asset_versions WHERE id=generation_tasks.final_version_id) AS final_height,(SELECT auto_overall FROM app.compliance_checks WHERE id=generation_tasks.compliance_id) AS auto_overall,app.is_manual_detection_delivery(compliance_id) AS detection_degraded"


def serialize(row, replay=False):
    data = dict(row)
    data["source"] = {
        "asset_id": str(data.pop("source_asset_id")),
        "version_id": str(data.pop("source_asset_version_id")),
    }
    data["credits"] = {"status": data.pop("credits_status"), "amount": data.pop("reserved_credits")}
    version = data.pop("final_version_id")
    check = data.pop("compliance_id")
    sha = data.pop("final_sha256")
    width = data.pop("final_width")
    height = data.pop("final_height")
    overall = data.pop("auto_overall")
    degraded = data.pop("detection_degraded")
    data["result"] = (
        {
            "asset_version_id": str(version),
            "sha256": sha,
            "width": width,
            "height": height,
            "download_endpoint": "/api/v1/tasks/" + str(data["id"]) + "/download",
            "report_id": str(check),
        }
        if data["status"] == "succeeded"
        else None
    )
    data["compliance"] = (
        {"auto_overall": overall, "report_id": str(check), "detection_degraded": degraded}
        if check
        else None
    )
    data["idempotent_replay"] = replay
    return data


@router.post("", status_code=201)
async def submit(
    body: TaskRequest,
    request: Request,
    response: Response,
    idempotency_key: str = Header(min_length=1, max_length=200),
    provider_confirmation: str | None = Header(default=None, alias="X-Provider-Confirmation"),
    user=Depends(current_user),
):
    business = QuoteRequest.model_validate(body.model_dump(exclude={"quote_id"}))
    input_hash = canonical_hash(business)
    request_hash = hash_data(
        {
            "operation": "generate",
            "quote_id": str(body.quote_id),
            "input": business.model_dump(mode="json", exclude_none=True),
        }
    )
    proposed = uuid4()
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        profile = await c.fetchval(
            "SELECT input_snapshot->'_execution'->>'provider_profile' FROM app.quotes WHERE id=$1",
            body.quote_id,
        )
        if profile == "bailian_live" and provider_confirmation != "bailian_live":
            raise HTTPException(409, "live_provider_confirmation_required")
        try:
            identity = await c.fetchval(
                "SELECT app.submit_quoted_task($1,$2,$3,$4,$5)",
                proposed,
                body.quote_id,
                idempotency_key,
                request_hash,
                input_hash,
            )
        except asyncpg.RaiseError as exc:
            db_conflict(exc)
        row = await c.fetchrow(
            "SELECT " + COLUMNS + " FROM app.generation_tasks WHERE id=$1", identity
        )
    replay = identity != proposed
    if replay:
        response.status_code = 200
    return {**serialize(row, replay), "request_id": request.state.request_id}


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
            "SELECT "
            + COLUMNS
            + " FROM app.generation_tasks WHERE ($1::timestamptz IS NULL OR (created_at,id)<($1,$2::uuid)) ORDER BY created_at DESC,id DESC LIMIT $3",
            pos[0] if pos else None,
            pos[1] if pos else None,
            limit + 1,
        )
    return {
        "items": [serialize(x) for x in rows[:limit]],
        "next_cursor": cursor_encode(rows[limit - 1]) if len(rows) > limit else None,
    }


@router.get("/{task_id}")
async def detail(task_id: UUID, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        row = await c.fetchrow(
            "SELECT " + COLUMNS + " FROM app.generation_tasks WHERE id=$1", task_id
        )
    if not row:
        raise HTTPException(404, "not_found")
    return serialize(row)


@router.post("/{task_id}/cancel")
async def cancel(task_id: UUID, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        try:
            await c.execute("SELECT app.cancel_task($1)", task_id)
        except asyncpg.RaiseError as exc:
            db_conflict(exc)
        row = await c.fetchrow(
            "SELECT " + COLUMNS + " FROM app.generation_tasks WHERE id=$1", task_id
        )
    return serialize(row)


@router.get("/{task_id}/compliance")
async def compliance(
    task_id: UUID, request: Request, check_id: UUID | None = None, user=Depends(current_user)
):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        row = await c.fetchrow(
            "SELECT c.id,c.final_version_id,c.final_sha256,c.facts,c.rule_snapshots,c.per_platform_results,c.auto_overall,c.sampling_selected,c.report_sha256,c.created_at,app.is_manual_detection_delivery(c.id) AS detection_degraded FROM app.compliance_checks c JOIN app.generation_tasks t ON t.id=c.task_id AND t.user_id=c.user_id WHERE t.id=$1 AND (($2::uuid IS NULL AND c.id=t.compliance_id) OR c.id=$2)",
            task_id,
            check_id,
        )
    if not row:
        raise HTTPException(404, "not_found")
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        review = await c.fetchrow(
            "SELECT id,reviewer_id,decision,reason,evidence_refs,created_at FROM app.compliance_reviews WHERE check_id=$1 ORDER BY created_at DESC,id DESC LIMIT 1",
            row["id"],
        )
    effective = (
        {"approved": "compliant", "rejected": "non_compliant", "needs_review": "manual_review"}[
            review["decision"]
        ]
        if review
        else row["auto_overall"]
    )
    return {
        "effective_overall": effective,
        "effective_source": "human_review" if review else "automatic",
        "latest_review": {**dict(review), "evidence_refs": json.loads(review["evidence_refs"])}
        if review
        else None,
        **dict(row),
        "facts": json.loads(row["facts"]),
        "rule_snapshots": json.loads(row["rule_snapshots"]),
        "per_platform_results": json.loads(row["per_platform_results"]),
    }


@router.get("/{task_id}/download")
async def download_final(task_id: UUID, request: Request, user=Depends(current_user)):
    import asyncio

    from fastapi.responses import RedirectResponse

    storage = request.app.state.storage
    if storage is None:
        raise HTTPException(503, "storage_not_configured")
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        row = await c.fetchrow(
            "SELECT v.bucket,v.object_key,v.storage_version_id FROM app.generation_tasks t JOIN app.asset_versions v ON v.id=t.final_version_id AND v.user_id=t.user_id WHERE t.id=$1 AND t.status='succeeded'",
            task_id,
        )
    if not row:
        raise HTTPException(404, "not_found")
    if row["bucket"] != storage.bucket:
        raise HTTPException(503, "storage_bucket_unavailable")
    return RedirectResponse(
        await asyncio.to_thread(
            storage.download_url, row["object_key"], row["storage_version_id"], "final.png"
        ),
        status_code=302,
    )


@router.get("/{task_id}/report/download")
async def download_report(
    task_id: UUID, request: Request, check_id: UUID | None = None, user=Depends(current_user)
):
    import asyncio

    from fastapi.responses import RedirectResponse

    storage = request.app.state.storage
    if storage is None:
        raise HTTPException(503, "storage_not_configured")
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        row = await c.fetchrow(
            "SELECT c.report_bucket,c.report_object_key,c.report_storage_version_id FROM app.generation_tasks t JOIN app.compliance_checks c ON c.task_id=t.id AND c.user_id=t.user_id WHERE t.id=$1 AND t.status='succeeded' AND (($2::uuid IS NULL AND c.id=t.compliance_id) OR c.id=$2)",
            task_id,
            check_id,
        )
    if not row:
        raise HTTPException(404, "not_found")
    if row["report_bucket"] != storage.bucket:
        raise HTTPException(503, "storage_bucket_unavailable")
    return RedirectResponse(
        await asyncio.to_thread(
            storage.download_url,
            row["report_object_key"],
            row["report_storage_version_id"],
            "report.json",
        ),
        status_code=302,
    )


@router.get("/{task_id}/events")
async def events(
    task_id: UUID,
    request: Request,
    last_event_id: str | None = Header(default=None),
    cursor: str | None = None,
    user=Depends(current_user),
):
    import asyncio

    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import StreamingResponse

    cursor_source = (
        "last-event-id"
        if last_event_id is not None
        else "query"
        if cursor is not None
        else "initial"
    )
    try:
        # Native EventSource supplies the header on reconnect. A new instance
        # after page reload can only supply its persisted cursor in the URL.
        # The newer reconnect header must take precedence over that initial URL.
        position = int(last_event_id if last_event_id is not None else cursor or "0")
        if position < 0 or position > 9223372036854775807:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "invalid_event_cursor")
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        if not await c.fetchval("SELECT id FROM app.generation_tasks WHERE id=$1", task_id):
            raise HTTPException(404, "not_found")
    # Record only normalized protocol metadata after authorization. No cookies,
    # tokens, DSNs or raw request headers enter the structured connection trace.
    logging.getLogger("uvicorn.error").info(
        json.dumps(
            {
                "event": "task_event_stream_open",
                "task_id": str(task_id),
                "cursor": str(position),
                "cursor_source": cursor_source,
                "request_id": getattr(request.state, "request_id", None),
            }
        )
    )

    async def stream():
        nonlocal position
        heartbeat = 0
        acknowledged_prune = 0
        while not await request.is_disconnected():
            try:
                await current_user(request)
            except HTTPException:
                return
            async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
                bounds = await c.fetchrow(
                    "SELECT min(id) AS first,max(id) AS last,(SELECT pruned_through FROM app.task_event_retention_cursors WHERE task_id=$1) AS pruned_through FROM app.task_events WHERE task_id=$1",
                    task_id,
                )
                reset = requires_event_reset(
                    position,
                    bounds["first"],
                    bounds["last"],
                    bounds["pruned_through"] or 0,
                    acknowledged_prune,
                )
                if reset:
                    position = 0
                acknowledged_prune = max(acknowledged_prune, bounds["pruned_through"] or 0)
                rows = await c.fetch(
                    "SELECT id,type,payload FROM app.task_events WHERE task_id=$1 AND id>$2 ORDER BY id LIMIT 100",
                    task_id,
                    position,
                )
                status = await c.fetchval(
                    "SELECT status FROM app.generation_tasks WHERE id=$1", task_id
                )
            # No connection or transaction is held while streaming or waiting.
            if reset:
                yield "id:\nevent: stream.reset\ndata: {}\n\n"
            for row in rows:
                position = row["id"]
                payload = json.loads(row["payload"])
                data = {**payload, "task_id": str(task_id)}
                yield (
                    "id: "
                    + str(position)
                    + "\nevent: "
                    + row["type"]
                    + "\ndata: "
                    + json.dumps(jsonable_encoder(data), ensure_ascii=False)
                    + "\n\n"
                )
            if status in ("succeeded", "failed", "cancelled") and len(rows) < 100:
                return
            heartbeat += 1
            if heartbeat >= 15:
                yield ": heartbeat\n\n"
                heartbeat = 0
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/{task_id}/input")
async def task_input(task_id: UUID, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        snapshot = await c.fetchval(
            "SELECT input_snapshot FROM app.generation_tasks WHERE id=$1", task_id
        )
    if snapshot is None:
        raise HTTPException(404, "not_found")
    result = json.loads(snapshot)
    result.pop("_execution", None)
    return result


@router.get("/{task_id}/compliance/versions")
async def report_versions(
    task_id: UUID, request: Request, cursor: str | None = None, user=Depends(current_user)
):
    pos = cursor_decode(cursor) if cursor else None
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as c:
        original = await c.fetchval(
            "SELECT compliance_id FROM app.generation_tasks WHERE id=$1", task_id
        )
        if not original:
            raise HTTPException(404, "not_found")
        rows = await c.fetch(
            "SELECT id,auto_overall,final_version_id,final_sha256,report_sha256,created_at FROM app.compliance_checks WHERE task_id=$1 AND ($2::timestamptz IS NULL OR (created_at,id)<($2,$3::uuid)) ORDER BY created_at DESC,id DESC LIMIT 21",
            task_id,
            pos[0] if pos else None,
            pos[1] if pos else None,
        )
    return {
        "items": [{**dict(row), "is_original": row["id"] == original} for row in rows[:20]],
        "next_cursor": cursor_encode(rows[19]) if len(rows) > 20 else None,
    }
