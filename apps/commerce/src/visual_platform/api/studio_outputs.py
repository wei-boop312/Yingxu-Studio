import asyncio
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4, uuid5

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from visual_platform.api.auth import current_user
from visual_platform.db.transactions import tenant_transaction
from visual_platform.domain.studio.contracts import (
    CaseWrite,
    DeliveryWrite,
    ImportOutput,
    ReviewWrite,
    digest,
)
from visual_platform.services import studio, studio_files
from visual_platform.storage.s3 import StorageError

router = APIRouter(prefix="/api/v1/studio", tags=["studio"])


def storage_for(request, row):
    storage = request.app.state.storage
    if storage is None or row["bucket"] != storage.bucket:
        raise HTTPException(503, "storage_bucket_unavailable")
    return storage


def chosen_output(view, identity):
    output = next((o for o in view["outputs"] if o["id"] == identity), None)
    if not output:
        raise HTTPException(404, "not_found")
    return output


def active_plan(view, slot_id, plan_id):
    slot = next((s for s in view["slots"] if s["id"] == slot_id), None)
    if (
        not slot
        or slot["plan_id"] != plan_id
        or slot["plan_stale"]
        or slot["readiness"]["status"] != "ready"
    ):
        raise HTTPException(409, "plan_stale_or_evidence_missing")
    return slot


@router.post("/projects/{project_id}/slots/{slot_id}/outputs", status_code=201)
async def import_output(
    project_id: UUID,
    slot_id: UUID,
    body: ImportOutput,
    request: Request,
    idempotency_key: str = Header(min_length=1, max_length=200),
    user=Depends(current_user),
):
    request_hash = digest(
        {"project": str(project_id), "slot": str(slot_id), "input": body.model_dump(mode="json")}
    )
    pool = request.app.state.api_pool
    async with tenant_transaction(pool, user["id"]) as conn:
        project = await studio.project(conn, project_id, lock=True)
        previous = await conn.fetchrow(
            "SELECT * FROM app.studio_outputs WHERE idempotency_key=$1", idempotency_key
        )
        if previous:
            if previous["request_hash"] != request_hash:
                raise HTTPException(409, "idempotency_conflict")
            return {"id": previous["id"], "idempotent_replay": True}
        view = await studio.view(conn, project_id)
        slot = active_plan(view, slot_id, body.plan_id)
        if slot["plan"]["route"] != body.route:
            raise HTTPException(409, "plan_route_mismatch")
        source = await conn.fetchrow(
            "SELECT * FROM app.asset_versions WHERE id=$1 AND product_id=$2",
            body.source_asset_version_id,
            project["product_id"],
        )
        if not source:
            raise HTTPException(422, "source_unavailable")
        source = dict(source)
    storage = storage_for(request, source)
    raw, _, _ = await asyncio.to_thread(
        storage.get, source["object_key"], version=source["storage_version_id"]
    )
    try:
        async with request.app.state.decode_capacity:
            data, mime = await asyncio.to_thread(
                studio_files.candidate, raw, source["sha256"], project["output_spec"]
            )
    except ValueError:
        raise HTTPException(422, "candidate_file_rejected") from None
    identity = uuid5(user["id"], "studio-import:" + idempotency_key)
    version = uuid5(identity, "candidate")
    asset = uuid5(identity, "asset")
    key = f"studio/{user['id']}/{project_id}/{version}"
    try:
        storage_version = await asyncio.to_thread(storage.put_immutable, key, data, mime)
    except StorageError:
        raise HTTPException(409, "idempotency_conflict") from None
    sha = hashlib.sha256(data).hexdigest()
    async with tenant_transaction(pool, user["id"]) as conn:
        await studio.project(conn, project_id, lock=True)
        previous = await conn.fetchrow(
            "SELECT * FROM app.studio_outputs WHERE idempotency_key=$1", idempotency_key
        )
        if previous:
            if previous["request_hash"] != request_hash:
                raise HTTPException(409, "idempotency_conflict")
            return {"id": previous["id"], "idempotent_replay": True}
        active_plan(await studio.view(conn, project_id), slot_id, body.plan_id)
        await conn.execute(
            "SELECT app.register_studio_candidate($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
            version,
            asset,
            project_id,
            body.source_asset_version_id,
            storage.bucket,
            key,
            storage_version,
            sha,
            project["output_spec"]["width"],
            project["output_spec"]["height"],
            len(data),
            mime,
        )
        provenance = {
            "source_note": body.source_note,
            "source_sha256": source["sha256"],
            "candidate_sha256": sha,
            "real_model_execution": False,
            "route": body.route,
            "source_files": [],
        }
        await conn.execute(
            """INSERT INTO app.studio_outputs(id,user_id,product_id,project_id,slot_id,plan_id,asset_version_id,
            source_asset_version_id,route,provenance,idempotency_key,request_hash)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12)""",
            identity,
            user["id"],
            project["product_id"],
            project_id,
            slot_id,
            body.plan_id,
            version,
            body.source_asset_version_id,
            body.route,
            json.dumps(provenance),
            idempotency_key,
            request_hash,
        )
        await conn.execute(
            "UPDATE app.studio_slots SET current_output_id=$2 WHERE id=$1 AND project_id=$3",
            slot_id,
            identity,
            project_id,
        )
        revision = await studio.bump(conn, project_id)
        return {
            "id": identity,
            "sha256": sha,
            "review_state": "pending",
            "project_revision": revision,
            "idempotent_replay": False,
        }


@router.post("/outputs/{output_id}/reviews", status_code=201)
async def review_output(
    output_id: UUID, body: ReviewWrite, request: Request, user=Depends(current_user)
):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        row = await conn.fetchrow(
            "SELECT project_id FROM app.studio_outputs WHERE id=$1", output_id
        )
        if not row:
            raise HTTPException(404, "not_found")
        await studio.project(conn, row["project_id"], lock=True, expected=body.expected_revision)
        view = await studio.view(conn, row["project_id"])
        output = chosen_output(view, output_id)
        slot = active_plan(view, output["slot_id"], output["plan_id"])
        basis = {
            "output_sha256": output["sha256"],
            "plan_id": str(output["plan_id"]),
            "plan_sha256": slot["plan_sha256"],
            "fact_version_ids": [f["id"] for f in slot["plan"]["facts"]],
            "platform_rule_status": "unverified_manual_review_required",
        }
        revision = await conn.fetchval(
            "SELECT COALESCE(max(revision),0)+1 FROM app.studio_reviews WHERE output_id=$1",
            output_id,
        )
        identity = uuid4()
        await conn.execute(
            """INSERT INTO app.studio_reviews(id,user_id,project_id,output_id,revision,decision,critical,checks,notes,basis)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10::jsonb)""",
            identity,
            user["id"],
            row["project_id"],
            output_id,
            revision,
            body.decision,
            body.critical,
            json.dumps(body.checks.model_dump(by_alias=True)),
            body.notes,
            json.dumps(basis),
        )
        project_revision = await studio.bump(conn, row["project_id"])
        return {"id": identity, "decision": body.decision, "project_revision": project_revision}


async def private_output(conn, identity):
    row = await conn.fetchrow(
        """SELECT v.* FROM app.studio_outputs o JOIN app.asset_versions v ON v.id=o.asset_version_id WHERE o.id=$1""",
        identity,
    )
    if not row:
        raise HTTPException(404, "not_found")
    return dict(row)


@router.get("/outputs/{output_id}/preview")
async def preview(output_id: UUID, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        row = await private_output(conn, output_id)
    storage_for(request, row)
    data, _, _ = await asyncio.to_thread(
        request.app.state.storage.get, row["object_key"], version=row["storage_version_id"]
    )
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise HTTPException(409, "output_integrity_mismatch")
    return Response(data, media_type=row["mime"])


@router.get("/references/{reference_id}/preview")
async def reference_preview(reference_id: UUID, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        row = await conn.fetchrow(
            "SELECT v.* FROM app.studio_references r JOIN app.asset_versions v ON v.id=r.asset_version_id WHERE r.id=$1",
            reference_id,
        )
        if not row:
            raise HTTPException(404, "not_found")
    storage_for(request, row)
    data, _, _ = await asyncio.to_thread(
        request.app.state.storage.get, row["object_key"], version=row["storage_version_id"]
    )
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise HTTPException(409, "output_integrity_mismatch")
    return Response(data, media_type=row["mime"])


@router.get("/outputs/{output_id}/download")
async def draft_download(output_id: UUID, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        row = await private_output(conn, output_id)
    storage_for(request, row)
    return RedirectResponse(
        request.app.state.storage.download_url(
            row["object_key"],
            row["storage_version_id"],
            filename="draft-" + str(output_id) + (".png" if row["mime"] == "image/png" else ".jpg"),
        ),
        status_code=302,
        headers={"X-Delivery-Qualification": "draft"},
    )


def validate_selection(view, body):
    selected = [chosen_output(view, identity) for identity in body.output_ids]
    if any(o["slot_id"] not in {s["id"] for s in view["slots"]} for o in selected):
        raise HTTPException(409, "slot_archived")
    if len({o["slot_id"] for o in selected}) != len(selected):
        raise HTTPException(422, "one_output_per_slot_required")
    if body.mode == "formal" and any(not o["eligible"] for o in selected):
        raise HTTPException(409, "formal_delivery_review_required")
    return selected


async def prepare_delivery(
    project_id: UUID,
    body: DeliveryWrite,
    request: Request,
    idempotency_key: str = Header(min_length=1, max_length=200),
    user=Depends(current_user),
):
    pool = request.app.state.api_pool
    request_hash = digest({"project_id": str(project_id), "input": body.model_dump(mode="json")})
    identity = uuid5(user["id"], "studio-delivery:" + idempotency_key)
    async with tenant_transaction(pool, user["id"]) as conn:
        project = await studio.project(conn, project_id, lock=True)
        prior = await conn.fetchrow(
            "SELECT * FROM app.studio_deliveries WHERE idempotency_key=$1", idempotency_key
        )
        if prior:
            if prior["request_hash"] != request_hash:
                raise HTTPException(409, "idempotency_conflict")
            return {
                "id": prior["id"],
                "status": prior["status"],
                "manifest": studio.document(prior["manifest"]),
                "idempotent_replay": True,
            }
        if project["revision"] != body.expected_revision:
            raise HTTPException(409, "revision_conflict")
        view = await studio.view(conn, project_id)
        selected = validate_selection(view, body)
        if sum(o["byte_size"] for o in selected) > 80 * 1024 * 1024:
            raise HTTPException(422, "delivery_size_limit_exceeded")
        sku = await studio.sku(conn, project["sku_id"])
        by_slot = {s["id"]: s for s in view["slots"]}
        included = {o["slot_id"] for o in selected}
        records = []
        private = []
        for output in selected:
            slot = by_slot[output["slot_id"]]
            records.append(
                {
                    "output_id": str(output["id"]),
                    "slot_id": str(slot["id"]),
                    "slot_number": slot["display_number"],
                    "filename": studio_files.filename(project, sku, slot, output),
                    "sha256": output["sha256"],
                    "asset_version_id": str(output["asset_version_id"]),
                    "review_id": str(output["review_id"]) if body.mode == "formal" else None,
                    "plan_id": str(output["plan_id"]),
                    "width": output["width"],
                    "height": output["height"],
                    "mime": output["mime"],
                    "byte_size": output["byte_size"],
                    "source_files": [],
                    "route": output["route"],
                    "real_model_execution": output["provenance"].get("real_model_execution", False),
                }
            )
            private.append(await private_output(conn, output["id"]))
        manifest = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "delivery_id": str(identity),
            "project_id": str(project_id),
            "sku_id": str(project["sku_id"]),
            "sku_code": sku["code"],
            "marketplace": project["marketplace"],
            "language": project["language"],
            "image_use": project["image_use"],
            "platform": project["platform"],
            "project_revision": project["revision"],
            "mode": body.mode,
            "scope": "complete" if len(included) == len(view["slots"]) else "partial",
            "delivered_count": len(selected),
            "total_slots": len(view["slots"]),
            "files": records,
            "pending_slots": [
                {
                    "slot_id": str(s["id"]),
                    "slot_number": s["display_number"],
                    "reason": "待资料：" + ", ".join(s["readiness"]["reasons"])
                    if s["readiness"]["status"] != "ready"
                    else "未纳入本次交付",
                }
                for s in view["slots"]
                if s["id"] not in included
            ],
            "platform_rule_status": "unverified_manual_review_required",
        }
        await conn.execute(
            """INSERT INTO app.studio_deliveries(id,user_id,project_id,project_revision,mode,status,manifest,idempotency_key,request_hash)
            VALUES($1,$2,$3,$4,$5,'preparing',$6::jsonb,$7,$8)""",
            identity,
            user["id"],
            project_id,
            project["revision"],
            body.mode,
            json.dumps(manifest, ensure_ascii=False),
            idempotency_key,
            request_hash,
        )
    try:
        files = []
        for meta, item in zip(private, records, strict=True):
            storage_for(request, meta)
            data, _, _ = await asyncio.to_thread(
                request.app.state.storage.get,
                meta["object_key"],
                version=meta["storage_version_id"],
            )
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise HTTPException(409, "output_integrity_mismatch")
            files.append((item["filename"], data))
        package = await asyncio.to_thread(studio_files.archive, manifest, files)
        sha = hashlib.sha256(package).hexdigest()
        key = f"studio-deliveries/{user['id']}/{identity}.zip"
        storage_version = await asyncio.to_thread(
            request.app.state.storage.put_immutable, key, package, "application/zip"
        )
        async with tenant_transaction(pool, user["id"]) as conn:
            await studio.project(conn, project_id, lock=True, expected=body.expected_revision)
            validate_selection(await studio.view(conn, project_id), body)
            for output in selected:
                await conn.execute(
                    "INSERT INTO app.studio_delivery_items VALUES($1,$2,$3,$4,$5)",
                    user["id"],
                    project_id,
                    identity,
                    output["id"],
                    output["review_id"] if body.mode == "formal" else None,
                )
            await conn.execute(
                "UPDATE app.studio_deliveries SET status='ready',object_key=$2,sha256=$3,bucket=$4,storage_version_id=$5 WHERE id=$1",
                identity,
                key,
                sha,
                request.app.state.storage.bucket,
                storage_version,
            )
    except BaseException as error:
        async with tenant_transaction(pool, user["id"]) as conn:
            await conn.execute(
                "UPDATE app.studio_deliveries SET status='failed' WHERE id=$1 AND status='preparing'",
                identity,
            )
        if isinstance(error, (StorageError, BotoCoreError, ClientError)):
            raise HTTPException(503, "dependency_unavailable") from None
        raise
    return {"id": identity, "status": "ready", "manifest": manifest, "sha256": sha}


@router.post("/projects/{project_id}/deliveries", status_code=201)
async def deliver(
    project_id: UUID,
    body: DeliveryWrite,
    request: Request,
    idempotency_key: str = Header(min_length=1, max_length=200),
    user=Depends(current_user),
):
    async with request.app.state.studio_export_capacity:
        return await prepare_delivery(project_id, body, request, idempotency_key, user)


@router.get("/deliveries/{delivery_id}/download")
async def download_delivery(delivery_id: UUID, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM app.studio_deliveries WHERE id=$1 AND status='ready'", delivery_id
        )
        if not row:
            raise HTTPException(404, "not_found")
    storage_for(request, row)
    data, _, _ = await asyncio.to_thread(
        request.app.state.storage.get,
        row["object_key"],
        version=row["storage_version_id"],
        limit=100 * 1024 * 1024,
    )
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise HTTPException(409, "output_integrity_mismatch")
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="studio-{delivery_id}.zip"'},
    )


@router.post("/projects/{project_id}/cases", status_code=201)
async def save_case(
    project_id: UUID, body: CaseWrite, request: Request, user=Depends(current_user)
):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        view = await studio.view(conn, project_id)
        delivery = next(
            (
                d
                for d in view["deliveries"]
                if d["id"] == body.delivery_id and d["status"] == "ready" and d["mode"] == "formal"
            ),
            None,
        )
        if not delivery:
            raise HTTPException(409, "formal_delivery_required")
        snapshot = {
            "delivery": delivery["manifest"],
            "marketing_data": None,
            "reuse_policy": "Rebind product evidence and review; no SKU facts or approvals inherited.",
        }
        identity = uuid4()
        await conn.execute(
            "INSERT INTO app.studio_cases(id,user_id,project_id,delivery_id,title,lessons,snapshot) VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)",
            identity,
            user["id"],
            project_id,
            body.delivery_id,
            body.title,
            body.lessons,
            json.dumps(snapshot, ensure_ascii=False),
        )
        return {"id": identity, **snapshot}


@router.get("/cases")
async def list_cases(request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        return {
            "items": [
                {**dict(r), "snapshot": studio.document(r["snapshot"])}
                for r in await conn.fetch(
                    "SELECT * FROM app.studio_cases ORDER BY created_at DESC LIMIT 200"
                )
            ]
        }
