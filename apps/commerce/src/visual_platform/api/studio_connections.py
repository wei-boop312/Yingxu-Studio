import importlib.util
from uuid import UUID, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from visual_platform.api.auth import current_user
from visual_platform.api.studio_outputs import active_plan
from visual_platform.db.transactions import tenant_transaction
from visual_platform.domain.studio.contracts import ExecutionCreate, digest
from visual_platform.providers.comfyui import ComfyUnavailable, LocalComfy
from visual_platform.services import studio

router = APIRouter(prefix="/api/v1/studio", tags=["studio"])


@router.get("/connections/local")
async def connection_state(request: Request, user=Depends(current_user)):
    return {
        "configured": bool(request.app.state.settings.studio_comfy_url),
        "generation_template_verified": False,
        "execution_enabled": False,
    }


@router.post("/connections/local/diagnose")
async def diagnose(request: Request, user=Depends(current_user)):
    target = request.app.state.settings.studio_comfy_url
    if not target:
        raise HTTPException(409, "comfy_connection_not_configured")
    client = LocalComfy(target)
    try:
        summary, _ = await client.diagnose()
        return {"configured": True, "reachable": True, **summary}
    except ComfyUnavailable:
        raise HTTPException(503, "comfy_connection_unavailable") from None
    finally:
        await client.close()


def public_intent(row):
    return {
        key: row[key]
        for key in (
            "id",
            "project_id",
            "slot_id",
            "plan_id",
            "prompt_id",
            "execution_kind",
            "status",
            "cancel_requested",
            "created_at",
            "updated_at",
        )
    } | {"result": studio.document(row["result"]), "real_model_execution": False}


@router.post("/projects/{project_id}/slots/{slot_id}/executions", status_code=201)
async def create_execution(
    project_id: UUID,
    slot_id: UUID,
    body: ExecutionCreate,
    request: Request,
    idempotency_key: str = Header(min_length=1, max_length=200),
    user=Depends(current_user),
):
    target = request.app.state.settings.studio_comfy_url
    if not target or importlib.util.find_spec("workflow_architect") is None:
        raise HTTPException(409, "comfy_runtime_not_configured")
    request_hash = digest(
        {
            "project_id": str(project_id),
            "slot_id": str(slot_id),
            "input": body.model_dump(mode="json"),
        }
    )
    identity = uuid5(user["id"], "studio-execution:" + idempotency_key)
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        project = await studio.project(conn, project_id, lock=True)
        prior = await conn.fetchrow(
            "SELECT * FROM app.studio_execution_intents WHERE idempotency_key=$1", idempotency_key
        )
        if prior:
            if prior["request_hash"] != request_hash:
                raise HTTPException(409, "idempotency_conflict")
            return {**public_intent(prior), "idempotent_replay": True}
        if project["revision"] != body.expected_revision:
            raise HTTPException(409, "revision_conflict")
        slot = active_plan(await studio.view(conn, project_id), slot_id, body.plan_id)
        if slot["plan"]["route"] != "comfyui":
            raise HTTPException(409, "plan_route_mismatch")
        if not any(
            r["asset_version_id"] == str(body.source_asset_version_id)
            and r["role"] == "product"
            and r["confirmed"]
            for r in slot["plan"]["references"]
        ):
            raise HTTPException(422, "source_not_in_frozen_plan")
        active = await conn.fetchrow(
            "SELECT * FROM app.studio_execution_intents WHERE slot_id=$1 AND status NOT IN ('succeeded','failed','cancelled') ORDER BY created_at LIMIT 1",
            slot_id,
        )
        if active:
            if active["request_hash"] != request_hash:
                raise HTTPException(409, "comfy_existing_execution_requires_reconciliation")
            return {**public_intent(active), "idempotent_replay": True}
        row = await conn.fetchrow(
            """INSERT INTO app.studio_execution_intents
        (id,user_id,product_id,project_id,slot_id,plan_id,source_asset_version_id,target_url,prompt_id,execution_kind,request_hash,idempotency_key)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,'connection_test',$10,$11) RETURNING *""",
            identity,
            user["id"],
            project["product_id"],
            project_id,
            slot_id,
            body.plan_id,
            body.source_asset_version_id,
            target,
            uuid5(identity, "prompt"),
            request_hash,
            idempotency_key,
        )
        return {**public_intent(row), "idempotent_replay": False}


@router.get("/executions/{identity}")
async def get_execution(identity: UUID, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM app.studio_execution_intents WHERE id=$1", identity
        )
        if not row:
            raise HTTPException(404, "not_found")
        return public_intent(row)


@router.post("/executions/{identity}/cancel")
async def cancel_execution(identity: UUID, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        if not await conn.fetchval(
            "SELECT id FROM app.studio_execution_intents WHERE id=$1", identity
        ):
            raise HTTPException(404, "not_found")
        return studio.document(await conn.fetchval("SELECT app.cancel_studio_intent($1)", identity))
