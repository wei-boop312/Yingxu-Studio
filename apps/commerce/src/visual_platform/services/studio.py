"""Shared Studio reads and invariant checks; all callers use tenant transactions."""

import json

from fastapi import HTTPException

from visual_platform.domain.studio.contracts import readiness


def document(value):
    return json.loads(value) if isinstance(value, str) else value


async def sku(conn, identity, *, lock=False):
    row = await conn.fetchrow(
        "SELECT * FROM app.studio_skus WHERE id=$1" + (" FOR UPDATE" if lock else ""), identity
    )
    if not row:
        raise HTTPException(404, "not_found")
    return dict(row)


async def project(conn, identity, *, lock=False, expected=None):
    row = await conn.fetchrow("SELECT * FROM app.studio_projects WHERE id=$1", identity)
    if not row:
        raise HTTPException(404, "not_found")
    if lock:
        await sku(conn, row["sku_id"], lock=True)
        row = await conn.fetchrow(
            "SELECT * FROM app.studio_projects WHERE id=$1 FOR UPDATE", identity
        )
    if expected is not None and expected != row["revision"]:
        raise HTTPException(409, "revision_conflict")
    result = dict(row)
    result["output_spec"] = document(result["output_spec"])
    return result


async def facts(conn, sku_id):
    rows = await conn.fetch(
        """SELECT DISTINCT ON(fact_key) * FROM app.studio_fact_versions
        WHERE sku_id=$1 ORDER BY fact_key,revision DESC""",
        sku_id,
    )
    result = []
    for row in rows:
        item = dict(row)
        item["appearance_snapshot"] = document(item["appearance_snapshot"])
        item["reference_ids"] = [
            r["reference_id"]
            for r in await conn.fetch(
                "SELECT reference_id FROM app.studio_fact_sources WHERE fact_version_id=$1",
                row["id"],
            )
        ]
        result.append(item)
    return result


async def references(conn, sku_id):
    return [
        dict(r)
        for r in await conn.fetch(
            """SELECT r.*,v.sha256,v.width,v.height
        FROM app.studio_references r JOIN app.asset_versions v ON v.id=r.asset_version_id
        WHERE r.sku_id=$1 ORDER BY r.created_at,r.id""",
            sku_id,
        )
    ]


async def bump(conn, project_id):
    return await conn.fetchval(
        "UPDATE app.studio_projects SET revision=revision+1 WHERE id=$1 RETURNING revision",
        project_id,
    )


async def view(conn, identity):
    data = await project(conn, identity)
    current_facts = await facts(conn, data["sku_id"])
    refs = await references(conn, data["sku_id"])
    current_ids = {str(f["id"]) for f in current_facts}
    slots = []
    for row in await conn.fetch(
        """SELECT s.*,p.id AS plan_id,p.revision AS plan_revision,p.snapshot,p.sha256 AS plan_sha256
        FROM app.studio_slots s LEFT JOIN LATERAL
        (SELECT * FROM app.studio_plans WHERE slot_id=s.id ORDER BY revision DESC LIMIT 1) p ON true
        WHERE s.project_id=$1 ORDER BY s.display_number""",
        identity,
    ):
        item = dict(row)
        snapshot = document(item.pop("snapshot"))
        item["plan"] = snapshot
        item["plan_stale"] = bool(
            snapshot and any(f["id"] not in current_ids for f in snapshot["facts"])
        )
        item["readiness"] = readiness(item, current_facts, refs)
        slots.append(item)
    outputs = []
    by_slot = {s["id"]: s for s in slots}
    for row in await conn.fetch(
        """SELECT o.*,v.sha256,v.width,v.height,v.mime,v.byte_size,
        r.id AS review_id,r.decision,r.critical,r.checks,r.notes AS review_notes,r.basis
        FROM app.studio_outputs o JOIN app.asset_versions v ON v.id=o.asset_version_id
        LEFT JOIN LATERAL (SELECT * FROM app.studio_reviews WHERE output_id=o.id ORDER BY revision DESC LIMIT 1) r ON true
        WHERE o.project_id=$1 ORDER BY o.created_at DESC,o.id""",
        identity,
    ):
        item = dict(row)
        slot = by_slot[item["slot_id"]]
        item["provenance"] = document(item["provenance"])
        item["checks"] = document(item["checks"])
        item["basis"] = document(item["basis"])
        item["eligible"] = bool(
            item["decision"] == "passed"
            and not slot["archived"]
            and slot["current_output_id"] == item["id"]
            and not item["critical"]
            and not slot["plan_stale"]
            and slot["plan_id"] == item["plan_id"]
            and item["basis"]
            and item["basis"].get("output_sha256") == item["sha256"]
        )
        item["review_state"] = (
            "stale"
            if item["review_id"]
            and (
                slot["plan_stale"]
                or slot["plan_id"] != item["plan_id"]
                or slot["current_output_id"] != item["id"]
            )
            else item["decision"] or "pending"
        )
        outputs.append(item)
    deliveries = []
    for row in await conn.fetch(
        "SELECT * FROM app.studio_deliveries WHERE project_id=$1 ORDER BY created_at DESC", identity
    ):
        item = dict(row)
        item["manifest"] = document(item["manifest"])
        item.pop("object_key")
        deliveries.append(item)
    data.update(
        facts=current_facts,
        references=refs,
        slots=[s for s in slots if not s["archived"]],
        archived_slots=[s for s in slots if s["archived"]],
        outputs=outputs,
        deliveries=deliveries,
    )
    return data
