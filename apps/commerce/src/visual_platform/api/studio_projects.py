import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from visual_platform.api.auth import current_user
from visual_platform.db.transactions import tenant_transaction
from visual_platform.domain.studio.contracts import (
    FactWrite,
    PlanWrite,
    ProjectCreate,
    ReferenceCreate,
    SkuCreate,
    SlotScopeWrite,
    default_slots,
    digest,
    readiness,
)
from visual_platform.services import studio

router = APIRouter(prefix="/api/v1/studio", tags=["studio"])


@router.get("/skus")
async def list_skus(request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        return {
            "items": [
                dict(r)
                for r in await conn.fetch(
                    "SELECT * FROM app.studio_skus ORDER BY created_at DESC LIMIT 200"
                )
            ]
        }


@router.post("/skus", status_code=201)
async def create_sku(body: SkuCreate, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        if not await conn.fetchval(
            "SELECT id FROM app.products WHERE id=$1 AND archived_at IS NULL", body.product_id
        ):
            raise HTTPException(404, "not_found")
        if await conn.fetchval(
            "SELECT id FROM app.studio_skus WHERE product_id=$1 AND code=$2",
            body.product_id,
            body.code,
        ):
            raise HTTPException(409, "sku_code_exists")
        return dict(
            await conn.fetchrow(
                """INSERT INTO app.studio_skus(id,user_id,product_id,code,name)
            VALUES($1,$2,$3,$4,$5) RETURNING *""",
                uuid4(),
                user["id"],
                body.product_id,
                body.code,
                body.name,
            )
        )


@router.get("/skus/{sku_id}/evidence")
async def evidence(sku_id: UUID, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        await studio.sku(conn, sku_id)
        return {
            "facts": await studio.facts(conn, sku_id),
            "references": await studio.references(conn, sku_id),
        }


@router.post("/skus/{sku_id}/references", status_code=201)
async def create_reference(
    sku_id: UUID, body: ReferenceCreate, request: Request, user=Depends(current_user)
):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        sku = await studio.sku(conn, sku_id, lock=True)
        source = await conn.fetchrow(
            "SELECT * FROM app.asset_versions WHERE id=$1 AND product_id=$2",
            body.asset_version_id,
            sku["product_id"],
        )
        if not source:
            raise HTTPException(422, "reference_scope_mismatch")
        row = await conn.fetchrow(
            """INSERT INTO app.studio_references
            (id,user_id,product_id,sku_id,asset_version_id,role,physical_face,confirmed,note)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *""",
            uuid4(),
            user["id"],
            sku["product_id"],
            sku_id,
            body.asset_version_id,
            body.role,
            body.physical_face,
            body.confirmed,
            body.note,
        )
        await conn.execute(
            "UPDATE app.studio_projects SET revision=revision+1 WHERE sku_id=$1", sku_id
        )
        return dict(row)


@router.post("/skus/{sku_id}/facts", status_code=201)
async def write_fact(sku_id: UUID, body: FactWrite, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        current_sku = await studio.sku(conn, sku_id, lock=True)
        prior = await conn.fetchrow(
            "SELECT * FROM app.studio_fact_versions WHERE sku_id=$1 AND fact_key=$2 ORDER BY revision DESC LIMIT 1",
            sku_id,
            body.fact_key,
        )
        if body.expected_revision != (prior["revision"] if prior else 0):
            raise HTTPException(409, "revision_conflict")
        refs = [r for r in await studio.references(conn, sku_id) if r["id"] in body.reference_ids]
        if len(refs) != len(set(body.reference_ids)):
            raise HTTPException(422, "reference_scope_mismatch")
        if body.status == "confirmed" and any(
            not r["confirmed"] or r["role"] in ("style", "scene") for r in refs
        ):
            raise HTTPException(422, "confirmed_product_evidence_required")
        appearance_snapshot = {}
        if body.appearance_profile_id:
            profile = await conn.fetchrow(
                "SELECT * FROM app.appearance_profiles WHERE id=$1 AND archived_at IS NULL",
                body.appearance_profile_id,
            )
            config = studio.document(profile["config"]) if profile else {}
            if (
                not profile
                or profile["product_id"] != current_sku["product_id"]
                or body.fact_key != "appearance"
                or config.get("description") != body.value
                or config.get("source_asset_version_id")
                not in {str(r["asset_version_id"]) for r in refs}
            ):
                raise HTTPException(422, "appearance_source_mismatch")
            appearance_snapshot = {
                "profile_id": str(profile["id"]),
                "revision": profile["revision"],
                "config": config,
                "origin": "ai_understanding_confirmed"
                if config.get("understanding_id")
                else "manual_profile",
            }
        identity = uuid4()
        row = await conn.fetchrow(
            """INSERT INTO app.studio_fact_versions
            (id,user_id,sku_id,fact_key,revision,label,value,status,conditions,appearance_snapshot)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb) RETURNING *""",
            identity,
            user["id"],
            sku_id,
            body.fact_key,
            body.expected_revision + 1,
            body.label,
            body.value,
            body.status,
            body.conditions,
            json.dumps(appearance_snapshot, ensure_ascii=False),
        )
        for ref in refs:
            await conn.execute(
                "INSERT INTO app.studio_fact_sources VALUES($1,$2,$3,$4)",
                user["id"],
                sku_id,
                identity,
                ref["id"],
            )
        impacted = [
            dict(r)
            for r in await conn.fetch(
                """SELECT DISTINCT p.project_id,p.slot_id FROM app.studio_plans p
            JOIN app.studio_plan_facts pf ON pf.plan_id=p.id JOIN app.studio_fact_versions f ON f.id=pf.fact_version_id
            WHERE p.sku_id=$1 AND f.fact_key=$2
            AND p.revision=(SELECT max(q.revision) FROM app.studio_plans q WHERE q.slot_id=p.slot_id)""",
                sku_id,
                body.fact_key,
            )
        ]
        if impacted:
            await conn.execute(
                "UPDATE app.studio_projects SET revision=revision+1 WHERE id=ANY($1::uuid[])",
                list({r["project_id"] for r in impacted}),
            )
        return {**dict(row), "impacted_slots": impacted}


@router.get("/projects")
async def list_projects(request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        rows = await conn.fetch("""SELECT p.*,s.code AS sku_code,s.name AS sku_name FROM app.studio_projects p
            JOIN app.studio_skus s ON s.id=p.sku_id ORDER BY p.created_at DESC LIMIT 200""")
        return {
            "items": [{**dict(r), "output_spec": studio.document(r["output_spec"])} for r in rows]
        }


@router.post("/projects", status_code=201)
async def create_project(body: ProjectCreate, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        sku = await studio.sku(conn, body.sku_id, lock=True)
        identity = uuid4()
        await conn.execute(
            """INSERT INTO app.studio_projects
            (id,user_id,product_id,sku_id,title,platform,marketplace,language,category,image_use,output_spec)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb)""",
            identity,
            user["id"],
            sku["product_id"],
            body.sku_id,
            body.title,
            body.platform,
            body.marketplace,
            body.language,
            body.category,
            body.image_use,
            body.output.model_dump_json(),
        )
        for slot in default_slots(body.slot_count, body.template):
            await conn.execute(
                """INSERT INTO app.studio_slots(id,user_id,project_id,display_number,title,purpose,required_fact_keys)
                VALUES($1,$2,$3,$4,$5,$6,$7)""",
                uuid4(),
                user["id"],
                identity,
                slot["display_number"],
                slot["title"],
                slot["purpose"],
                slot["required_fact_keys"],
            )
        return await studio.view(conn, identity)


@router.get("/projects/{project_id}")
async def read_project(project_id: UUID, request: Request, user=Depends(current_user)):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        return await studio.view(conn, project_id)


@router.put("/projects/{project_id}/slots")
async def change_slot_scope(
    project_id: UUID, body: SlotScopeWrite, request: Request, user=Depends(current_user)
):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        await studio.project(conn, project_id, lock=True, expected=body.expected_revision)
        rows = await conn.fetch(
            "SELECT id FROM app.studio_slots WHERE project_id=$1 ORDER BY display_number",
            project_id,
        )
        known = [row["id"] for row in rows]
        if not set(body.slot_ids) <= set(known):
            raise HTTPException(422, "slot_scope_mismatch")
        await conn.execute("SET CONSTRAINTS ALL DEFERRED")
        for index, identity in enumerate(known, start=100001):
            await conn.execute(
                "UPDATE app.studio_slots SET archived=true,display_number=$2 WHERE id=$1",
                identity,
                index,
            )
        for number, identity in enumerate(body.slot_ids, start=1):
            await conn.execute(
                "UPDATE app.studio_slots SET archived=false,display_number=$2 WHERE id=$1",
                identity,
                number,
            )
        for number, addition in enumerate(body.additions, start=len(body.slot_ids) + 1):
            required = ["appearance"] + (
                ["dimensions"]
                if addition.purpose == "dimensions"
                else ["package_list"]
                if addition.purpose == "package"
                else []
            )
            await conn.execute(
                "INSERT INTO app.studio_slots(id,user_id,project_id,display_number,title,purpose,required_fact_keys) VALUES($1,$2,$3,$4,$5,$6,$7)",
                uuid4(),
                user["id"],
                project_id,
                number,
                addition.title,
                addition.purpose,
                required,
            )
        await studio.bump(conn, project_id)
        return await studio.view(conn, project_id)


@router.post("/projects/{project_id}/slots/{slot_id}/plans", status_code=201)
async def freeze_plan(
    project_id: UUID, slot_id: UUID, body: PlanWrite, request: Request, user=Depends(current_user)
):
    async with tenant_transaction(request.app.state.api_pool, user["id"]) as conn:
        project = await studio.project(conn, project_id, lock=True, expected=body.expected_revision)
        slot = await conn.fetchrow(
            "SELECT * FROM app.studio_slots WHERE project_id=$1 AND id=$2", project_id, slot_id
        )
        if not slot or slot["archived"]:
            raise HTTPException(404, "not_found")
        current = await studio.facts(conn, project["sku_id"])
        refs = await studio.references(conn, project["sku_id"])
        state = readiness(slot, current, refs)
        if state["status"] != "ready":
            raise HTTPException(409, "slot_evidence_missing")
        selected = [f for f in current if f["id"] in body.fact_version_ids]
        selected_refs = [r for r in refs if r["id"] in body.reference_ids]
        if len(selected) != len(set(body.fact_version_ids)) or any(
            f["status"] != "confirmed" for f in selected
        ):
            raise HTTPException(409, "fact_version_stale_or_unconfirmed")
        if not set(slot["required_fact_keys"]) <= {f["fact_key"] for f in selected}:
            raise HTTPException(422, "plan_required_facts_missing")
        if len(selected_refs) != len(set(body.reference_ids)):
            raise HTTPException(422, "reference_scope_mismatch")
        faces = {
            r["physical_face"]
            for r in selected_refs
            if r["role"] == "product" and r["confirmed"] and r["physical_face"] != "UNKNOWN"
        }
        if not set(body.allowed_faces) <= faces:
            raise HTTPException(422, "unknown_face_not_allowed")
        if not {ref for f in selected for ref in f["reference_ids"]} <= {
            r["id"] for r in selected_refs
        }:
            raise HTTPException(422, "plan_fact_evidence_missing")
        snapshot = {
            "schema_version": 1,
            "sku_id": str(project["sku_id"]),
            "project_id": str(project_id),
            "slot_id": str(slot_id),
            "goal": body.goal,
            "copy": body.copy_text,
            "allowed_faces": body.allowed_faces,
            "route": body.route,
            "marketplace": project["marketplace"],
            "language": project["language"],
            "image_use": project["image_use"],
            "output_spec": project["output_spec"],
            "platform_rule_status": "unverified_manual_review_required",
            "appearance_description": next(
                f["value"] for f in selected if f["fact_key"] == "appearance"
            ),
            "facts": [
                {
                    k: str(f[k]) if k == "id" else f[k]
                    for k in (
                        "id",
                        "fact_key",
                        "revision",
                        "label",
                        "value",
                        "status",
                        "conditions",
                        "appearance_snapshot",
                    )
                }
                for f in selected
            ],
            "references": [
                {
                    k: str(r[k]) if k in ("id", "asset_version_id") else r[k]
                    for k in (
                        "id",
                        "asset_version_id",
                        "sha256",
                        "role",
                        "physical_face",
                        "confirmed",
                    )
                }
                for r in selected_refs
            ],
        }
        revision = await conn.fetchval(
            "SELECT COALESCE(max(revision),0)+1 FROM app.studio_plans WHERE slot_id=$1", slot_id
        )
        identity = uuid4()
        await conn.execute(
            "INSERT INTO app.studio_plans(id,user_id,sku_id,project_id,slot_id,revision,snapshot,sha256) VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8)",
            identity,
            user["id"],
            project["sku_id"],
            project_id,
            slot_id,
            revision,
            json.dumps(snapshot, ensure_ascii=False),
            digest(snapshot),
        )
        for fact in selected:
            await conn.execute(
                "INSERT INTO app.studio_plan_facts VALUES($1,$2,$3,$4)",
                user["id"],
                project["sku_id"],
                identity,
                fact["id"],
            )
        for ref in selected_refs:
            await conn.execute(
                "INSERT INTO app.studio_plan_references VALUES($1,$2,$3,$4)",
                user["id"],
                project["sku_id"],
                identity,
                ref["id"],
            )
        project_revision = await studio.bump(conn, project_id)
        return {
            "id": identity,
            "revision": revision,
            "sha256": digest(snapshot),
            "snapshot": snapshot,
            "project_revision": project_revision,
        }
