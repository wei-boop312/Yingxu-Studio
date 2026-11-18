import hashlib
import json
from uuid import uuid4

from fastapi import HTTPException

from visual_platform.db.transactions import tenant_transaction
from visual_platform.domain.schemas import QuoteRequest, canonical_hash


def hash_data(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def db_conflict(exc):
    code = str(exc).split("\n")[0]
    invisible = {
        "quote_not_found",
        "product_unavailable",
        "source_unavailable",
        "appearance_unavailable",
        "task_not_found",
        "upload_not_found",
    }
    known = invisible | {
        "insufficient_credits",
        "idempotency_conflict",
        "quote_consumed",
        "quote_expired",
        "quote_input_mismatch",
        "inactive_user",
        "already_started",
        "source_hash_mismatch",
        "upload_busy",
        "upload_rejected",
        "upload_expired",
        "upload_lease_lost",
        "provider_runtime_revoked",
        "unregistered_runtime_configuration",
        "registered_runtime_binding_mismatch",
        "runtime_profile_not_enabled",
        "live_provider_not_authorized",
    }
    if code in known:
        raise HTTPException(404 if code in invisible else 409, code)
    raise exc


async def create_quote(pool, uid, body: QuoteRequest, *, live=False):
    import asyncpg

    business = body.model_dump(mode="json", exclude_none=True)
    async with tenant_transaction(pool, uid) as c:
        source = await c.fetchrow(
            """SELECT v.id,v.sha256,v.width,v.height FROM app.asset_versions v
         JOIN app.assets a ON a.user_id=v.user_id AND a.id=v.asset_id
         WHERE v.id=$1 AND v.product_id=$2 AND v.canonical AND a.status='ready'""",
            body.source_asset_version_id,
            body.product_id,
        )
        if not source:
            raise HTTPException(404, "source_unavailable")
        g = body.generation
        if g.protection_mode == "pixel_exact":
            if (g.output.width, g.output.height) != (source["width"], source["height"]):
                raise HTTPException(422, "pixel_exact_size_mismatch")
            for rect in g.regions:
                if (
                    rect.source_x + rect.width > source["width"]
                    or rect.source_y + rect.height > source["height"]
                ):
                    raise HTTPException(422, "source_rectangle_out_of_bounds")
        appearance = None
        if body.appearance_profile_id:
            row = await c.fetchrow(
                "SELECT id,name,config,revision FROM app.appearance_profiles WHERE id=$1 AND product_id=$2 AND archived_at IS NULL",
                body.appearance_profile_id,
                body.product_id,
            )
            if not row:
                raise HTTPException(404, "appearance_unavailable")
            appearance = {**dict(row), "id": str(row["id"]), "config": json.loads(row["config"])}
            bound_source = appearance["config"].get("source_asset_version_id")
            if bound_source and bound_source != str(body.source_asset_version_id):
                raise HTTPException(409, "appearance_source_mismatch")
        rules = []
        for target in body.targets:
            row = await c.fetchrow(
                """SELECT id,version,evidence_status,config,config_sha256,source_urls,retrieved_at,evidence_sha256 FROM app.platform_rules
             WHERE platform=$1 AND marketplace=$2 AND category=$3 AND image_type=$4 AND published_at IS NOT NULL
             ORDER BY published_at DESC,id DESC LIMIT 1""",
                target.platform,
                target.marketplace,
                target.category,
                target.image_type,
            )
            if row:
                rule = {
                    **target.model_dump(),
                    "rule_id": str(row["id"]),
                    "version": row["version"],
                    "evidence_status": row["evidence_status"],
                    "config": json.loads(row["config"]),
                    "config_sha256": row["config_sha256"],
                    "evidence_sha256": row["evidence_sha256"],
                    "source_urls": json.loads(row["source_urls"]),
                    "retrieved_at": row["retrieved_at"].isoformat()
                    if row["retrieved_at"]
                    else None,
                }
            else:
                rule = {
                    **target.model_dump(),
                    "version": "draft-20261112",
                    "evidence_status": "unverified",
                    "config": {},
                    "source_urls": [],
                }
            rules.append(rule)
        execution = {
            "source_sha256": source["sha256"],
            "source_width": source["width"],
            "source_height": source["height"],
            "appearance": appearance,
            "provider_model": "fake-v1",
            "price_version": "trial-fake-v1",
            "rules": rules,
        }
        if live:
            from visual_platform.providers.qwen_image import checked_dimensions

            try:
                checked_dimensions(g.output.width, g.output.height)
                configuration = json.loads(
                    await c.fetchval("SELECT app.live_provider_configuration()")
                )
            except ValueError:
                raise HTTPException(422, "live_model_dimensions_unsupported") from None
            except asyncpg.RaiseError as exc:
                db_conflict(exc)
            execution.update(configuration["binding"])
            execution.update(
                provider_profile="bailian_live",
                provider_model="qwen-image-3.0",
                price_version="bailian-live-reference-v1",
                live_reference_rates=configuration["reference_rates"],
                live_price_evidence_sha256=configuration["price_evidence_sha256"],
            )
        snapshot = {**business, "_execution": execution}
        identity = uuid4()
        try:
            await c.fetchval(
                "SELECT app.create_quote($1,$2,$3,$4::jsonb,$5::jsonb)",
                identity,
                canonical_hash(body),
                hash_data(snapshot),
                json.dumps(snapshot, ensure_ascii=False),
                json.dumps(rules, ensure_ascii=False),
            )
        except asyncpg.RaiseError as exc:
            db_conflict(exc)
        row = await c.fetchrow(
            "SELECT id AS quote_id,credits,expires_at,input_hash,price_version,provider_model FROM app.quotes WHERE id=$1",
            identity,
        )
    result = dict(row)
    result["provider_profile"] = "bailian_live" if live else "offline"
    if live:
        from decimal import ROUND_CEILING, Decimal

        rates = configuration["reference_rates"]
        suffix = "1k" if g.output.width * g.output.height <= 2_250_000 else "2k"
        generation = Decimal(rates["generate"]["qima_input_" + suffix]) + Decimal(
            rates["generate"]["qima_output_" + suffix]
        )
        detection = (
            Decimal(991808) * Decimal(rates["detect"]["input_token"])
            + Decimal(8192) * Decimal(rates["detect"]["output_token"])
        ) / 1000000
        result["supplier_reference_upper_cny"] = str(
            (generation + detection).quantize(Decimal("0.00000001"), rounding=ROUND_CEILING)
        )
        result["supplier_actual_spending_verified"] = False
    return result
