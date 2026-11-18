import asyncio
import contextlib
import hashlib
import inspect
import json
from uuid import UUID, uuid4, uuid5

import asyncpg
from botocore.exceptions import BotoCoreError, ClientError

from visual_platform.domain.compliance import evaluate_rules
from visual_platform.domain.facts import Facts
from visual_platform.providers.contract import ProviderFailure, ProviderPending, ProviderUncertain
from visual_platform.services.image_job import transform
from visual_platform.services.upload import decode
from visual_platform.storage.s3 import StorageError


class Worker:
    def __init__(self, pool, storage, provider, owner=None):
        self.pool = pool
        self.storage = storage
        self.provider = provider
        self.owner = owner or str(uuid4())

    async def db(self, query, *args):
        async with self.pool.acquire() as c:
            return await c.fetchval(query, *args)

    async def context(self, task, fence):
        return json.loads(
            await self.db("SELECT app.worker_context($1,$2,$3)", task, self.owner, fence)
        )

    async def call_provider(self, method, *args):
        if inspect.iscoroutinefunction(method):
            return await method(*args)
        return await asyncio.to_thread(method, *args)

    async def cost(self, task, fence, attempt_id, stage):
        if getattr(self.provider, "execution_profile", "offline") == "offline":
            if stage == "response":
                await self.db(
                    "SELECT app.record_fake_cost($1,$2,$3,$4)", task, self.owner, fence, attempt_id
                )
            return
        usage = {
            "transport": "bailian_live"
            if getattr(self.provider, "execution_profile", "offline") == "bailian_live"
            else "offline_mock",
            "state": "submission_outcome_not_yet_known",
        }
        if stage == "response":
            ctx = await self.context(task, fence)
            if any(
                row["attempt_id"] == str(attempt_id) and row["kind"] == "response_observation"
                for row in ctx.get("cost_observations", [])
            ):
                await self.db(
                    "SELECT app.record_supplier_estimate($1,$2,$3,$4)",
                    task,
                    self.owner,
                    fence,
                    attempt_id,
                )
                return
            attempt = next(a for a in ctx["attempts"] if a["id"] == str(attempt_id))
            if attempt["step"] == "detect" and getattr(
                self.provider, "durable_detection_archive", False
            ):
                record = await self.detection_payload(task, fence, attempt)
                usage = self.provider.observation_from_facts(record)
            elif attempt["step"] == "generate" and getattr(
                self.provider, "durable_generation_archive", False
            ):
                record = await self.generation_payload(task, fence, attempt)
                usage = (
                    self.provider.observation_from_generation(record)
                    if record is not None
                    else await self.call_provider(self.provider.observation, "generate", attempt)
                )
            else:
                usage = await self.call_provider(
                    self.provider.observation, attempt["step"], attempt
                )
        await self.db(
            "SELECT app.record_protocol_observation($1,$2,$3,$4,$5,$6::jsonb)",
            task,
            self.owner,
            fence,
            attempt_id,
            stage,
            json.dumps(usage, sort_keys=True),
        )
        if stage == "response":
            await self.db(
                "SELECT app.record_supplier_estimate($1,$2,$3,$4)",
                task,
                self.owner,
                fence,
                attempt_id,
            )

    async def read(self, record):
        if record["bucket"] != self.storage.bucket:
            raise RuntimeError("bucket not configured")
        data, _, _ = await asyncio.to_thread(
            self.storage.get,
            record["object_key"],
            version=record["storage_version_id"],
            limit=80 * 1024 * 1024,
        )
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("asset_hash_mismatch")
        return data

    async def renew(self, task, fence):
        while True:
            await asyncio.sleep(20)
            await self.db("SELECT app.renew_lease($1,$2,$3)", task, self.owner, fence)

    async def preserve_detection(self, task, attempt, provider_id, digest):
        payload = await self.call_provider(self.provider.fetch_facts, provider_id)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        identity = await self.db(
            "SELECT app.append_detection_receipt($1,$2,$3,$4,$5,$6)",
            task,
            attempt,
            provider_id,
            digest,
            canonical,
            hashlib.sha256(canonical.encode()).hexdigest(),
        )
        return identity, payload

    async def preserve_generation(self, task, attempt, provider_id, digest):
        payload = await self.call_provider(self.provider.fetch_generation_record, attempt)
        if payload is None:
            return None, None
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        identity = await self.db(
            "SELECT app.append_generation_receipt($1,$2,$3,$4,$5,$6)",
            task,
            attempt,
            provider_id,
            digest,
            canonical,
            hashlib.sha256(canonical.encode()).hexdigest(),
        )
        return identity, payload

    async def generation_payload(self, task, fence, attempt):
        saved = await self.db(
            "SELECT app.generation_archive($1,$2,$3,$4)",
            task,
            self.owner,
            fence,
            UUID(attempt["id"]),
        )
        if saved is not None:
            return self.provider.validate_generation_record(json.loads(saved))
        # Only existing local evidence may fill a pre-migration gap; never submit again.
        try:
            _, payload = await self.preserve_generation(
                task, UUID(attempt["id"]), attempt["provider_id"], attempt["response_digest"]
            )
            return payload
        except ProviderUncertain:
            return None

    async def detection_payload(self, task, fence, attempt):
        if not getattr(self.provider, "durable_detection_archive", False):
            return await self.call_provider(self.provider.fetch_facts, attempt["provider_id"])
        saved = await self.db(
            "SELECT app.detection_archive($1,$2,$3,$4)",
            task,
            self.owner,
            fence,
            UUID(attempt["id"]),
        )
        if saved is not None:
            return self.provider.validate_facts_record(json.loads(saved))
        # A pre-migration receipt can be archived once from its existing local facts.
        _, payload = await self.preserve_detection(
            task, UUID(attempt["id"]), attempt["provider_id"], attempt["response_digest"]
        )
        return payload

    async def receipt(self, task, fence, attempt, receipt, *, step=None):
        if step == "detect" and getattr(self.provider, "durable_detection_archive", False):
            identity, _ = await self.preserve_detection(
                task, attempt, receipt.provider_id, receipt.response_digest
            )
        elif step == "generate" and getattr(self.provider, "durable_generation_archive", False):
            identity, _ = await self.preserve_generation(
                task, attempt, receipt.provider_id, receipt.response_digest
            )
            if identity is None:
                identity = await self.db(
                    "SELECT app.append_receipt($1,$2,$3,$4,$5)",
                    task,
                    attempt,
                    receipt.provider_id,
                    receipt.response_digest,
                    receipt.polling_url,
                )
        else:
            identity = await self.db(
                "SELECT app.append_receipt($1,$2,$3,$4,$5)",
                task,
                attempt,
                receipt.provider_id,
                receipt.response_digest,
                receipt.polling_url,
            )
        await self.db("SELECT app.apply_receipt($1,$2,$3,$4)", task, self.owner, fence, identity)
        await self.cost(task, fence, attempt, "response")

    async def submit(self, task, fence, step, payload):
        encoded = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(
                payload, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        )
        try:
            attempt = await self.db(
                "SELECT app.record_intent($1,$2,$3,$4,$5)",
                task,
                self.owner,
                fence,
                step,
                hashlib.sha256(encoded).hexdigest(),
            )
        except asyncpg.RaiseError as error:
            code = str(error).split("\n", 1)[0]
            if code not in {
                "provider_budget_blocked",
                "provider_budget_expired",
                "provider_budget_scope_mismatch",
                "provider_budget_exhausted",
                "provider_budget_cost_review_required",
                "provider_price_expired",
                "provider_runtime_revoked",
                "unregistered_runtime_configuration",
                "registered_runtime_binding_mismatch",
                "runtime_profile_not_enabled",
                "live_provider_not_authorized",
            }:
                raise
            needs_review = code == "provider_budget_cost_review_required"
            runtime_denial = code in {
                "provider_runtime_revoked",
                "unregistered_runtime_configuration",
                "registered_runtime_binding_mismatch",
                "runtime_profile_not_enabled",
                "live_provider_not_authorized",
            }
            if code == "provider_price_expired" or runtime_denial:
                context = await self.context(task, fence)
                needs_review = bool(context["attempts"])
            # The denied step rolled back intent and hold; earlier steps may already have cost.
            await self.db(
                "SELECT app.fail_task($1,$2,$3,$4,$5)",
                task,
                self.owner,
                fence,
                "provider_cost_review_required"
                if needs_review
                else "provider_runtime_denied"
                if runtime_denial
                else "provider_budget_denied",
                needs_review,
            )
            return False
        await self.cost(task, fence, attempt, "intent")
        try:
            method = (
                self.provider.submit_generate if step == "generate" else self.provider.submit_detect
            )
            result = await self.call_provider(method, attempt, payload)
        except ProviderFailure:
            await self.db(
                "SELECT app.record_attempt_failure($1,$2,$3,$4,$5)",
                task,
                self.owner,
                fence,
                attempt,
                "supplier_explicit_failure",
            )
            await self.db(
                "SELECT app.fail_task($1,$2,$3,$4,$5)",
                task,
                self.owner,
                fence,
                "detection_explicit_failure_review"
                if step == "detect"
                else "supplier_explicit_failure",
                step == "detect",
            )
            return False
        except Exception as exc:
            # Any unclassified exception after intent is potentially a paid/accepted submission.
            await self.db(
                "SELECT app.fail_task($1,$2,$3,$4,true)",
                task,
                self.owner,
                fence,
                "submission_response_timeout"
                if isinstance(exc, ProviderUncertain)
                and str(exc) in {"qwen_read_timeout", "qwen_write_timeout", "qwen_connect_timeout"}
                else "submission_outcome_unknown",
            )
            return False
        await self.receipt(task, fence, attempt, result, step=step)
        return True

    async def asset(self, task, fence, ctx, kind, data, meta):
        version = uuid5(task, kind)
        asset = uuid5(task, kind + "-asset")
        key = "private/" + ctx["user_id"] + "/" + str(version)
        storage_version = await asyncio.to_thread(
            self.storage.put_immutable, key, data, "image/png"
        )
        return await self.db(
            "SELECT app.record_task_asset($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)",
            task,
            self.owner,
            fence,
            kind,
            asset,
            version,
            self.storage.bucket,
            key,
            storage_version,
            hashlib.sha256(data).hexdigest(),
            meta["width"],
            meta["height"],
            len(data),
        )

    async def run(self, task_id):
        task = UUID(str(task_id))
        result = await self.db(
            "SELECT ops.claim_task($1,$2,$3)",
            task,
            self.owner,
            getattr(self.provider, "execution_profile", "offline"),
        )
        if result is None:
            return "not_claimed"
        claim = json.loads(result)
        if claim["status"] == "uncertain":
            return "uncertain"
        fence = claim["fencing_token"]
        heartbeat = asyncio.create_task(self.renew(task, fence))
        try:
            for _ in range(12):
                if heartbeat.done():
                    await heartbeat
                ctx = await self.context(task, fence)
                generation = ctx["input_snapshot"]["generation"]
                phase = ctx["phase"]
                execution = ctx["input_snapshot"]["_execution"]
                if execution.get("provider_profile", "offline") != getattr(
                    self.provider, "execution_profile", "offline"
                ):
                    raise ValueError("locked_provider_profile_mismatch")
                if hasattr(self.provider, "verify_execution_scope"):
                    self.provider.verify_execution_scope(execution)
                attempts = {a["step"]: a for a in ctx["attempts"]}
                if phase == "submitting":
                    # Validate fixed source bytes before creating a possibly paid intent.
                    source = await self.read(ctx["source"])
                    if "generate" in attempts:
                        await self.db(
                            "SELECT app.fail_task($1,$2,$3,$4,true)",
                            task,
                            self.owner,
                            fence,
                            "submission_outcome_unknown",
                        )
                        return "uncertain"
                    payload = (
                        await self.call_provider(
                            self.provider.prepare_generate,
                            generation,
                            source,
                            execution.get("appearance"),
                        )
                        if hasattr(self.provider, "prepare_generate")
                        else generation
                    )
                    if not await self.submit(task, fence, "generate", payload):
                        return "stopped"
                elif phase in ("polling", "archiving"):
                    attempt = attempts["generate"]
                    await self.cost(task, fence, UUID(attempt["id"]), "response")
                    if not attempt["provider_id"]:
                        raise RuntimeError("known provider id required")
                    if hasattr(self.provider, "retrieve_image"):
                        artifact = await self.call_provider(self.provider.retrieve_image, attempt)
                        data = artifact.data
                        mime = artifact.mime
                    else:
                        data = await self.call_provider(
                            self.provider.fetch_image, attempt["provider_id"]
                        )
                        mime = "image/png"
                    canonical, meta = await decode(data, mime)
                    if (meta["width"], meta["height"]) != (
                        generation["output"]["width"],
                        generation["output"]["height"],
                    ):
                        raise ValueError("generated_dimensions_mismatch")
                    if getattr(self.provider, "durable_generation_result_archive", False):
                        result_record = self.provider.prepare_generation_result(
                            attempt, artifact, canonical, meta
                        )
                        result_text = json.dumps(
                            result_record, sort_keys=True, separators=(",", ":"), allow_nan=False
                        )
                        await self.db(
                            "SELECT app.append_qwen_result($1,$2,$3,$4,$5,$6)",
                            task,
                            self.owner,
                            fence,
                            UUID(attempt["id"]),
                            result_text,
                            hashlib.sha256(result_text.encode()).hexdigest(),
                        )
                        # Qwen billing meters arrive on successful GET, not the initial POST.
                        await self.db(
                            "SELECT app.record_supplier_estimate($1,$2,$3,$4)",
                            task,
                            self.owner,
                            fence,
                            UUID(attempt["id"]),
                        )
                    await self.asset(task, fence, ctx, "generated", canonical, meta)
                elif phase == "protecting":
                    source = await self.read(ctx["source"])
                    generated = await self.read(ctx["generated"])
                    final, meta = await transform(source, generated, generation)
                    await self.asset(task, fence, ctx, "final", final, meta)
                elif phase in ("detecting", "validating_rules"):
                    final = await self.read(ctx["final"])
                    final_hash = hashlib.sha256(final).hexdigest()
                    degradation = ctx.get("detection_degradation")
                    if degradation is not None:
                        if (
                            phase != "validating_rules"
                            or degradation["final_version_id"] != ctx["final_version_id"]
                            or degradation["final_sha256"] != final_hash
                            or "detect" not in attempts
                            or degradation["attempt_id"] != attempts["detect"]["id"]
                        ):
                            raise ValueError("manual_detection_scope_mismatch")
                        facts = Facts.model_validate(degradation["facts"])
                    else:
                        if phase != "detecting":
                            raise ValueError("manual_detection_review_required")
                        if "detect" not in attempts:
                            if not await self.submit(task, fence, "detect", final):
                                return "stopped"
                            continue
                        attempt = attempts["detect"]
                        if not attempt["provider_id"]:
                            await self.db(
                                "SELECT app.fail_task($1,$2,$3,$4,true)",
                                task,
                                self.owner,
                                fence,
                                "detection_outcome_unknown",
                            )
                            return "uncertain"
                        await self.cost(task, fence, UUID(attempt["id"]), "response")
                        payload = await self.detection_payload(task, fence, attempt)
                        try:
                            facts = Facts.model_validate(payload["facts"])
                            if payload.get("final_sha256") != final_hash or (
                                facts.image_width,
                                facts.image_height,
                            ) != (ctx["final"]["width"], ctx["final"]["height"]):
                                raise ValueError("facts mismatch")
                        except (ValueError, KeyError, TypeError):
                            facts = Facts.model_validate(
                                {
                                    "schema_version": 1,
                                    "analysis_complete": False,
                                    "image_width": ctx["final"]["width"],
                                    "image_height": ctx["final"]["height"],
                                    "text_items": [],
                                    "logo_items": [],
                                    "watermark_items": [],
                                    "background": {"classification": "unknown", "confidence": 0.0},
                                    "product_extent": None,
                                    "uncertainties": ["facts_invalid_or_final_image_mismatch"],
                                }
                            )
                    source = await self.read(ctx["source"])
                    _, proof = await transform(source, final, generation, verify=True)
                    rules = ctx["input_snapshot"]["_execution"]["rules"]
                    sampling = int(hashlib.sha256(str(task).encode()).hexdigest()[:8], 16) % 20 == 0
                    check = evaluate_rules(
                        final_hash,
                        rules,
                        facts.model_dump(mode="json"),
                        {
                            "width": ctx["final"]["width"],
                            "height": ctx["final"]["height"],
                            "byte_size": len(final),
                        },
                        sampling_selected=sampling,
                    )
                    for platform in check["per_platform"]:
                        platform["checks"].extend(
                            [
                                {"id": "local_decode", "status": "passed"},
                                {
                                    "id": "pixel_protection",
                                    "status": "passed"
                                    if proof["pixel_exact_passed"]
                                    else "not_applicable",
                                    "evidence": proof,
                                },
                            ]
                        )
                    report = {
                        "schema_version": 1,
                        "final_asset_version_id": ctx["final_version_id"],
                        "detection_degraded": degradation is not None,
                        "final_sha256": final_hash,
                        "facts": facts.model_dump(mode="json"),
                        "per_platform": check["per_platform"],
                        "auto_overall": check["auto_overall"],
                        "pixel_protection": proof,
                        "sampling_selected": sampling,
                        "provider_profile": getattr(
                            self.provider, "report_profile", "offline_fake"
                        ),
                    }
                    report_id = uuid5(task, "report")
                    key = "private/" + ctx["user_id"] + "/" + str(report_id) + ".json"
                    data = json.dumps(
                        report, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                    ).encode()
                    storage_version = await asyncio.to_thread(
                        self.storage.put_immutable, key, data, "application/json"
                    )
                    await self.db(
                        "SELECT app.record_report($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8,$9,$10,$11,$12,$13)",
                        task,
                        self.owner,
                        fence,
                        report_id,
                        final_hash,
                        json.dumps(report["facts"]),
                        json.dumps(report["per_platform"]),
                        report["auto_overall"],
                        report["sampling_selected"],
                        hashlib.sha256(data).hexdigest(),
                        self.storage.bucket,
                        key,
                        storage_version,
                    )
                elif phase == "finalizing":
                    await self.db("SELECT app.settle_task($1,$2,$3)", task, self.owner, fence)
                    return "succeeded"
                else:
                    raise RuntimeError("unsupported phase")
            raise RuntimeError("phase progress limit exceeded")
        except asyncpg.ObjectNotInPrerequisiteStateError:
            return "lease_lost"
        except (ProviderPending, ProviderUncertain):
            # A known receipt permits only safe polling/archive retries, never another submit.
            return "retryable"
        except ProviderFailure:
            attempt = attempts["generate" if phase in ("polling", "archiving") else "detect"]
            await self.db(
                "SELECT app.record_attempt_failure($1,$2,$3,$4,$5)",
                task,
                self.owner,
                fence,
                UUID(attempt["id"]),
                "supplier_terminal_failure",
            )
            await self.db(
                "SELECT app.fail_task($1,$2,$3,$4,$5)",
                task,
                self.owner,
                fence,
                "detection_terminal_failure_review"
                if attempt["step"] == "detect"
                else "supplier_terminal_failure",
                attempt["step"] == "detect",
            )
            return "stopped"
        except (BotoCoreError, ClientError, StorageError, RuntimeError, OSError):
            return "retryable"
        except ValueError:
            # Local validation failure is definite only if no unresolved paid attempt exists.
            try:
                await self.db(
                    "SELECT app.fail_task($1,$2,$3,$4,false)",
                    task,
                    self.owner,
                    fence,
                    "local_validation_failed",
                )
            except asyncpg.RaiseError:
                await self.db(
                    "SELECT app.fail_task($1,$2,$3,$4,true)",
                    task,
                    self.owner,
                    fence,
                    "delivery_requires_reconciliation",
                )
            return "stopped"
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncpg.PostgresError):
                await heartbeat
            await self.db("SELECT app.yield_lease($1,$2,$3)", task, self.owner, fence)
