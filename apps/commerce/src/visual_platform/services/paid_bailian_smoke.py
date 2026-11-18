"""One explicitly authorized local paid experiment, separate from app/Worker adapters.

Defaults to no network. A live round has a global authorization claim and at most
one POST for each model, even after an error. No database spending path is enabled.
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from visual_platform.domain.compliance import evaluate_rules
from visual_platform.domain.schemas import Generation
from visual_platform.providers.bailian_connection import BailianConnection, connection_status
from visual_platform.providers.bailian_metadata import probe_metadata
from visual_platform.providers.qwen_image import (
    ASYNC_PATH,
    MODEL,
    QwenImageRequest,
    strict_document,
    task_id,
    workspace_origin,
)
from visual_platform.providers.qwen_usage import image_usage
from visual_platform.providers.qwen_vision import MODEL as VISION_MODEL
from visual_platform.providers.qwen_vision import PATH as VISION_PATH
from visual_platform.providers.qwen_vision import VISION_SYSTEM, parse_visual_response
from visual_platform.services.image_job import transform
from visual_platform.services.upload import decode

ROOT = Path(__file__).resolve().parents[3]
DIGEST = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
MAX_INPUT_TOKENS = 991808
MAX_OUTPUT_TOKENS = 8192
MAX_JSON = 1024 * 1024
MAX_DOWNLOAD = 20 * 1024 * 1024
GLOBAL_CLAIM = ROOT / ".local" / "paid-bailian-user-20261115.claim"


def encoded(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def bounded_file(path, limit):
    path = Path(path)
    if path.is_symlink() or path.is_junction() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("bounded_regular_private_file_required")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("private_file_grew_beyond_limit")
    return raw


def save(path, raw, *, replay=False):
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if replay and bounded_file(path, MAX_DOWNLOAD) == raw:
            return
        raise ValueError("private_smoke_evidence_already_exists") from None
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


class PaidSmokeScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    authorization_id: UUID
    authorization_ref: Literal["user_20261115_bailian_test"]
    authorized_at: datetime
    expires_at: datetime
    connection_sha256: DIGEST
    source_path: str
    source_sha256: DIGEST
    original_path: str
    original_sha256: DIGEST
    generation: Generation
    generate_ceiling: Literal["0.30"] = "0.30"
    detect_ceiling: Literal["1.00"] = "1.00"
    total_ceiling: Literal["2.00"] = "2.00"
    max_generate_calls: Literal[1] = 1
    max_detect_calls: Literal[1] = 1

    @field_validator("max_generate_calls", "max_detect_calls", mode="before")
    @classmethod
    def strict_single_call(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("one_integer_call_required")
        return value

    @model_validator(mode="after")
    def limits(self):
        if any(
            value.tzinfo is None or value.utcoffset() != timedelta(0)
            for value in (self.authorized_at, self.expires_at)
        ) or not timedelta(0) < self.expires_at - self.authorized_at <= timedelta(hours=24):
            raise ValueError("explicit_bounded_utc_authorization_required")
        if self.generation.protection_mode != "pixel_exact" or not self.generation.regions:
            raise ValueError("reviewed_pixel_protection_required")
        return self


def load_paid_scope(path):
    return PaidSmokeScope.model_validate_json(encoded(strict_document(bounded_file(path, 65536))))


class DetectionSupplementApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    authorization_id: UUID
    approval_ref: Literal["user_20261115_permission_and_single_detection_supplement"]
    parent_scope_sha256: DIGEST
    prior_rejection_sha256: DIGEST
    final_sha256: DIGEST
    approved_at: datetime
    expires_at: datetime
    total_ceiling: Literal["2.00"] = "2.00"

    @model_validator(mode="after")
    def window(self):
        if any(
            value.tzinfo is None or value.utcoffset() != timedelta(0)
            for value in (self.approved_at, self.expires_at)
        ) or not timedelta(0) < self.expires_at - self.approved_at <= timedelta(hours=24):
            raise ValueError("bounded_supplement_approval_required")
        return self


def amount(value):
    return str(Decimal(value).quantize(Decimal("0.00000001"), rounding=ROUND_CEILING))


def reference_prices(body, model):
    rows = strict_document(body).get("output", {}).get("models")
    if type(rows) is not list or len(rows) != 1 or rows[0].get("model") != model:
        raise ValueError("exact_authenticated_price_model_required")
    groups = rows[0].get("prices")
    if type(groups) is not list or len(groups) != 1 or groups[0].get("range_name") != "Default":
        raise ValueError("unreviewed_price_tiers")
    desired = (
        {
            "qima_input_1k": "0.02",
            "qima_input_2k": "0.02",
            "qima_output_1k": "0.18",
            "qima_output_2k": "0.18",
        }
        if model == MODEL
        else {"input_token": "0.8", "output_token": "2.7", "input_token_cache": "0.1"}
    )

    unit = "每张" if model == MODEL else "每百万tokens"
    observed = {}
    for row in groups[0]["prices"]:
        name = row.get("type")
        if name not in desired:
            continue  # Batch and explicit cache creation are never requested.
        value = row.get("price")
        if (
            name in observed
            or type(value) is not str
            or not re.fullmatch(r"(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,8})?", value)
            or row.get("price_unit") != unit
            or row.get("time_band") != "standard"
            or Decimal(value) != Decimal(desired[name])
        ):
            raise ValueError("authenticated_prices_differ_from_reviewed_beijing_reference")
        observed[name] = value
    if set(observed) != set(desired):
        raise ValueError("incomplete_authenticated_reference_prices")
    return observed


def download_url(value):
    if type(value) is not str or len(value) > 8192:
        raise ValueError("unreviewed_result_url")
    url = urlsplit(value)
    # Exact regional examples plus the documented Qwen image-edit acceleration form.
    accelerated = (
        re.fullmatch(r"dashscope-[a-z0-9]{1,32}\.oss-accelerate\.aliyuncs\.com", url.hostname or "")
        is not None
    )
    if (
        url.scheme != "https"
        or (
            not accelerated
            and url.hostname
            not in {
                "dashscope-result-bj.oss-cn-beijing.aliyuncs.com",
                "dashscope-result-sz.oss-cn-shenzhen.aliyuncs.com",
            }
        )
        or url.username
        or url.password
        or url.port not in (None, 443)
        or url.fragment
    ):
        raise ValueError("unreviewed_result_url")
    return value


class SingleRound:
    def __init__(
        self,
        scope,
        connection,
        key,
        folder,
        *,
        live=False,
        transport=None,
        resume=False,
        sleeper=time.sleep,
    ):
        if (
            type(live) is not bool
            or (live and transport is not None)
            or (not live and not isinstance(transport, httpx.MockTransport))
        ):
            raise ValueError("explicit_live_or_explicit_mock_transport_required")
        self.scope = PaidSmokeScope.model_validate_json(scope.model_dump_json())
        self.connection = BailianConnection.model_validate(connection.model_dump(), strict=True)
        if (
            connection.digest() != scope.connection_sha256
            or not connection_status(connection, key)["credential_matches"]
        ):
            raise ValueError("bound_private_connection_required")
        self.origin = workspace_origin(connection.endpoint_origin)
        self.folder = Path(folder)
        self.live = live
        self.resume = resume
        self.transport = transport
        self.sleeper = sleeper
        self.key = key
        self.report = {
            "transport": "reviewed_paid_smoke" if live else "offline_mock",
            "authorization_sha256": sha(encoded(scope.model_dump(mode="json"))),
            "result": "NOT_STARTED",
            "paid_posts_attempted": 0,
            "mock_posts_attempted": 0,
            "real_model_output_exists": False,
            "actual_spending_verified": False,
            "application_live_routing_enabled": False,
        }
        if self.folder.is_symlink() or self.folder.is_junction() or not self.folder.is_dir():
            raise ValueError("existing_private_smoke_folder_required")
        if live and (
            folder.resolve()
            != (ROOT / ".local" / "paid-authorizations" / str(scope.authorization_id)).resolve()
        ):
            raise ValueError("bound_private_authorization_folder_required")
        if live:
            for parent in (self.folder, *self.folder.parents):
                if parent.is_symlink() or parent.is_junction():
                    raise ValueError("private_authorization_path_must_not_follow_links")
                if parent == ROOT:
                    break

    def valid_now(self):
        if not self.scope.authorized_at <= datetime.now(timezone.utc) < self.scope.expires_at:
            raise ValueError("authorization_expired_or_not_started")

    def exchange(self, client, method, url, name, *, payload=None, image=False):
        headers = {
            "Accept": "image/png" if image else "application/json",
            "Accept-Encoding": "identity",
        }
        if not image:
            if not url.startswith(self.origin + "/"):
                raise ValueError("authenticated_origin_mismatch")
            headers["Authorization"] = "Bearer " + self.key
            if method == "POST" and url.endswith(ASYNC_PATH):
                headers["X-DashScope-Async"] = "enable"
        if payload is not None:
            headers["Content-Type"] = "application/json"
        with client.stream(
            method, url, headers=headers, content=encoded(payload) if payload is not None else None
        ) as response:
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise ValueError("compressed_smoke_response_rejected")
            limit = MAX_DOWNLOAD if image else MAX_JSON
            data = bytearray()
            for chunk in response.iter_bytes(chunk_size=16384):
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("smoke_response_exceeds_limit")
            raw = bytes(data)
            save(self.folder / (name + ".body"), raw, replay=method == "GET")
            save(
                self.folder / (name + ".status.json"),
                encoded(
                    {
                        "status": response.status_code,
                        "content_type": response.headers.get("content-type", ""),
                        "body_sha256": sha(raw),
                    }
                ),
                replay=method == "GET",
            )
            if response.status_code != 200:
                raise ValueError("smoke_http_outcome_requires_review")
            wanted = "image/png" if image else "application/json"
            if response.headers.get("content-type", "").split(";", 1)[0].lower() != wanted:
                raise ValueError("smoke_content_type_mismatch")
            return raw

    def cached(self, step, limit, mime):
        raw = bounded_file(self.folder / (step + ".body"), limit)
        status = strict_document(bounded_file(self.folder / (step + ".status.json"), 4096))
        if (
            status.get("status") != 200
            or status.get("body_sha256") != sha(raw)
            or status.get("content_type", "").split(";", 1)[0].lower() != mime
        ):
            raise ValueError("existing_paid_response_not_confirmed_or_changed")
        return raw

    def post_once(self, client, step, path, payload):
        self.valid_now()
        request = encoded(payload)
        intent = self.folder / (step + ".intent")
        if intent.exists():
            if not self.resume or bounded_file(intent, 4096) != encoded(
                {
                    "request_sha256": sha(request),
                    "authorization_sha256": self.report["authorization_sha256"],
                }
            ):
                raise ValueError("paid_intent_already_attempted_do_not_resubmit")
            return self.cached(step, MAX_JSON, "application/json")
        if self.resume and step == "generation":
            raise ValueError("resume_requires_existing_known_task")
        claim = GLOBAL_CLAIM if self.live else self.folder / "mock-authorization.claim"
        save(
            claim,
            encoded(
                {
                    "authorization_id": str(self.scope.authorization_id),
                    "authorization_sha256": self.report["authorization_sha256"],
                }
            ),
            replay=self.resume or step == "detection",
        )
        save(self.folder / (step + ".request.json"), request)
        save(
            intent,
            encoded(
                {
                    "request_sha256": sha(request),
                    "authorization_sha256": self.report["authorization_sha256"],
                }
            ),
        )
        self.report["paid_posts_attempted" if self.live else "mock_posts_attempted"] += 1
        return self.exchange(client, "POST", self.origin + path, step, payload=payload)

    def run(self):
        try:
            return self.execute()
        except Exception as error:
            self.report.update(result="STOPPED_REQUIRES_REVIEW", error_type=type(error).__name__)
            # Controlled error codes only; never echo HTTP request headers or upstream messages.
            if isinstance(error, ValueError) and re.fullmatch(r"[a-z_]{1,100}", str(error)):
                self.report["reason"] = str(error)
            return self.report
        finally:
            count = sum(
                (self.folder / (step + ".intent")).is_file() for step in ("generation", "detection")
            )
            self.report["paid_posts_attempted" if self.live else "mock_posts_attempted"] = count
            save(self.folder / ("run-" + uuid4().hex + ".json"), encoded(self.report))

    def run_supplement(self, approval):
        """An explicit second user approval permits one new detector POST after a known 403.

        Original intents/responses are never reset, removed, or treated as successful.
        Even both detectors at the model context bound must fit the original total.
        """
        try:
            self.valid_now()
            approval = DetectionSupplementApproval.model_validate_json(approval.model_dump_json())
            now = datetime.now(timezone.utc)
            if (
                not approval.approved_at <= now < approval.expires_at
                or approval.expires_at > self.scope.expires_at
                or approval.authorization_id != self.scope.authorization_id
                or approval.parent_scope_sha256 != self.report["authorization_sha256"]
            ):
                raise ValueError("supplement_does_not_match_original_authority")
            claim = GLOBAL_CLAIM if self.live else self.folder / "mock-authorization.claim"
            if bounded_file(claim, 4096) != encoded(
                {
                    "authorization_id": str(self.scope.authorization_id),
                    "authorization_sha256": self.report["authorization_sha256"],
                }
            ):
                raise ValueError("original_round_claim_required")
            rejected_raw = bounded_file(self.folder / "detection.body", MAX_JSON)
            rejected_status = strict_document(
                bounded_file(self.folder / "detection.status.json", 4096)
            )
            error = strict_document(rejected_raw).get("error", {})
            if (
                rejected_status.get("status") != 403
                or rejected_status.get("body_sha256") != sha(rejected_raw)
                or approval.prior_rejection_sha256 != sha(rejected_raw)
                or error.get("code") != "access_denied"
                or error.get("message") != "Access denied by API-Key restrictions."
            ):
                raise ValueError("only_confirmed_api_key_policy_rejection_can_be_supplemented")
            source = bounded_file(self.scope.source_path, 10_000_000)
            original = bounded_file(self.scope.original_path, MAX_DOWNLOAD)
            final = bounded_file(self.folder / "final.png", 10_000_000)
            if (
                sha(source) != self.scope.source_sha256
                or sha(original) != self.scope.original_sha256
                or sha(final) != approval.final_sha256
            ):
                raise ValueError("supplement_source_or_final_changed")
            canonical, meta = asyncio.run(decode(final, "image/png"))
            if sha(canonical) != sha(final):
                raise ValueError("canonical_final_required")
            _, protection = asyncio.run(
                transform(source, final, self.scope.generation.model_dump(mode="json"), verify=True)
            )
            old_payload = strict_document(
                bounded_file(self.folder / "detection.request.json", MAX_DOWNLOAD)
            )
            old_intent = strict_document(bounded_file(self.folder / "detection.intent", 4096))
            if (
                old_intent
                != {
                    "authorization_sha256": self.report["authorization_sha256"],
                    "request_sha256": sha(encoded(old_payload)),
                }
                or old_payload.get("model") != VISION_MODEL
                or old_payload.get("max_tokens") != MAX_OUTPUT_TOKENS
                or old_payload.get("enable_thinking") is not False
                or old_payload.get("stream") is not False
                or set(old_payload)
                != {
                    "model",
                    "stream",
                    "enable_thinking",
                    "max_tokens",
                    "response_format",
                    "messages",
                }
            ):
                raise ValueError("supplement_must_reuse_exact_original_detection_request")
            embedded = old_payload["messages"][1]["content"][0]["image_url"]["url"]
            if embedded != "data:image/png;base64," + base64.b64encode(final).decode():
                raise ValueError("supplement_final_request_mismatch")
            self.cached("generation", MAX_JSON, "application/json")
            prices = {}
            for model in (MODEL, VISION_MODEL):
                report, body = probe_metadata(
                    self.connection, self.key, model=model, transport=self.transport
                )
                save(
                    self.folder / ("supplement-catalogue-" + model + "-" + uuid4().hex + ".body"),
                    body,
                )
                if report["result"] != "REMOTE_MODEL_METADATA_READ":
                    raise ValueError("authenticated_catalogue_preflight_failed")
                prices[model] = reference_prices(body, model)
            tier = "1k" if meta["width"] * meta["height"] <= 2250000 else "2k"
            generated_cost = Decimal(prices[MODEL]["qima_input_" + tier]) + Decimal(
                prices[MODEL]["qima_output_" + tier]
            )
            detection_upper = (
                Decimal(MAX_INPUT_TOKENS) * Decimal(prices[VISION_MODEL]["input_token"])
                + Decimal(MAX_OUTPUT_TOKENS) * Decimal(prices[VISION_MODEL]["output_token"])
            ) / 1000000
            if generated_cost + 2 * detection_upper > Decimal(
                approval.total_ceiling
            ) or detection_upper > Decimal(self.scope.detect_ceiling):
                raise ValueError("supplement_worst_case_exceeds_original_total")
            self.report.update(
                supplemental_detection_authorized=True,
                original_detection_http_status=403,
                combined_reference_upper_cny=amount(generated_cost + 2 * detection_upper),
                real_model_output_exists=self.live,
                final_sha256=sha(final),
                pixel_exact_passed=protection["pixel_exact_passed"],
            )
            approval_sha = sha(encoded(approval.model_dump(mode="json")))
            save(
                self.folder / "detection-supplement-approval.json",
                encoded(approval.model_dump(mode="json")),
                replay=True,
            )
            supplement_intent = self.folder / "detection-supplement.intent"
            intent_body = encoded(
                {
                    "authorization_sha256": self.report["authorization_sha256"],
                    "supplement_approval_sha256": approval_sha,
                    "request_sha256": sha(encoded(old_payload)),
                }
            )
            with httpx.Client(
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(60, connect=10),
            ) as client:
                if supplement_intent.exists():
                    if not self.resume or bounded_file(supplement_intent, 4096) != intent_body:
                        raise ValueError("supplement_already_attempted_do_not_resubmit")
                    raw = self.cached("detection-supplement", MAX_JSON, "application/json")
                else:
                    self.valid_now()
                    save(self.folder / "detection-supplement.request.json", encoded(old_payload))
                    save(supplement_intent, intent_body)
                    raw = self.exchange(
                        client,
                        "POST",
                        self.origin + VISION_PATH,
                        "detection-supplement",
                        payload=old_payload,
                    )
            facts, metering, response_id, integrity = parse_visual_response(
                raw, meta["width"], meta["height"]
            )
            save(
                self.folder / "detection-supplement-facts.json",
                encoded(
                    {
                        "facts": facts.model_dump(mode="json"),
                        **metering,
                        "response_id": response_id,
                        "structure_integrity": integrity,
                        "wire_response_sha256": sha(raw),
                        "final_sha256": sha(final),
                        "transport": self.report["transport"],
                    }
                ),
                replay=True,
            )
            self.report.update(
                result="SINGLE_ROUND_COMPLETED",
                detection_structure_integrity=integrity,
                detection_analysis_complete=facts.analysis_complete,
                detection_facts_schema_verified="qwen_facts_schema_or_response_unverified"
                not in facts.uncertainties
                and integrity == "valid",
                detection_metering=metering,
                manual_review_required=True,
                generation_reference_estimate_cny=amount(generated_cost),
            )
            if metering["quantities"] is not None:
                counts = metering["quantities"]
                detected_cost = (
                    Decimal(counts["input_tokens"]) * Decimal(prices[VISION_MODEL]["input_token"])
                    + Decimal(counts["cached_input_tokens"])
                    * Decimal(prices[VISION_MODEL]["input_token_cache"])
                    + Decimal(counts["output_tokens"])
                    * Decimal(prices[VISION_MODEL]["output_token"])
                ) / 1000000
                self.report["successful_detection_reference_estimate_cny"] = amount(detected_cost)
                self.report["successful_calls_reference_estimate_cny"] = amount(
                    generated_cost + detected_cost
                )
            if (
                sha(bounded_file(self.scope.source_path, 10_000_000)) != self.scope.source_sha256
                or sha(bounded_file(self.scope.original_path, MAX_DOWNLOAD))
                != self.scope.original_sha256
            ):
                raise ValueError("original_or_reference_changed_during_supplement")
            self.report["original_and_reference_unchanged"] = True
            return self.report
        except Exception as error:
            self.report.update(result="STOPPED_REQUIRES_REVIEW", error_type=type(error).__name__)
            if isinstance(error, ValueError) and re.fullmatch(r"[a-z_]{1,100}", str(error)):
                self.report["reason"] = str(error)
            return self.report
        finally:
            count = sum(
                (self.folder / (step + ".intent")).is_file()
                for step in ("generation", "detection", "detection-supplement")
            )
            self.report["paid_posts_attempted" if self.live else "mock_posts_attempted"] = count
            save(self.folder / ("supplement-run-" + uuid4().hex + ".json"), encoded(self.report))

    def execute(self):
        self.valid_now()
        source = bounded_file(self.scope.source_path, 10_000_000)
        original = bounded_file(self.scope.original_path, MAX_DOWNLOAD)
        if sha(source) != self.scope.source_sha256 or sha(original) != self.scope.original_sha256:
            raise ValueError("reviewed_source_or_original_changed")
        canonical, meta = asyncio.run(decode(source, "image/png"))
        if sha(canonical) != sha(source):
            raise ValueError("canonical_source_required")
        generation = self.scope.generation.model_dump(mode="json")
        request = QwenImageRequest.from_canonical(generation, source).payload()
        if (meta["width"], meta["height"]) != (
            self.scope.generation.output.width,
            self.scope.generation.output.height,
        ):
            raise ValueError("pixel_exact_source_and_output_dimensions_must_match")
        prices = {}
        for model in (MODEL, VISION_MODEL):
            report, body = probe_metadata(
                self.connection, self.key, model=model, transport=self.transport
            )
            save(self.folder / ("catalogue-" + model + "-" + uuid4().hex + ".body"), body)
            if report["result"] != "REMOTE_MODEL_METADATA_READ":
                raise ValueError("authenticated_catalogue_preflight_failed")
            prices[model] = reference_prices(body, model)
        tier = "1k" if meta["width"] * meta["height"] <= 2250000 else "2k"
        generation_prediction = Decimal(prices[MODEL]["qima_input_" + tier]) + Decimal(
            prices[MODEL]["qima_output_" + tier]
        )
        detection_upper = (
            Decimal(MAX_INPUT_TOKENS) * Decimal(prices[VISION_MODEL]["input_token"])
            + Decimal(MAX_OUTPUT_TOKENS) * Decimal(prices[VISION_MODEL]["output_token"])
        ) / 1000000
        if (
            generation_prediction > Decimal(self.scope.generate_ceiling)
            or detection_upper > Decimal(self.scope.detect_ceiling)
            or generation_prediction + detection_upper > Decimal(self.scope.total_ceiling)
        ):
            raise ValueError("reference_request_upper_bounds_exceed_authorized_caps")
        self.report.update(
            generation_reference_prediction_cny=amount(generation_prediction),
            detection_reference_upper_cny=amount(detection_upper),
            total_ceiling_cny=self.scope.total_ceiling,
            price_basis="authenticated_catalogue_numbers_and_official_beijing_cny_reference_not_invoice",
        )
        with httpx.Client(
            transport=self.transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(60, connect=10),
        ) as client:
            submit_raw = self.post_once(client, "generation", ASYNC_PATH, request)
            submitted = strict_document(submit_raw)
            if submitted.get("code") or submitted["output"]["task_status"] not in (
                "PENDING",
                "RUNNING",
                "SUCCEEDED",
            ):
                raise ValueError("generation_submit_outcome_unknown")
            identity = task_id(submitted["output"]["task_id"])
            result = None
            for _ in range(100):
                self.valid_now()
                result_raw = self.exchange(
                    client, "GET", self.origin + "/api/v1/tasks/" + identity, "query-" + uuid4().hex
                )
                result = strict_document(result_raw)
                if result.get("code") or result["output"]["task_id"] != identity:
                    raise ValueError("generation_query_identity_mismatch")
                status = result["output"]["task_status"]
                if status == "SUCCEEDED":
                    break
                if status not in ("PENDING", "RUNNING"):
                    raise ValueError("known_generation_task_not_successful")
                self.sleeper(3)
            else:
                raise ValueError("known_generation_task_still_pending_resume_get_only")
            choices = result["output"]["choices"]
            if (
                type(choices) is not list
                or len(choices) != 1
                or choices[0]["finish_reason"] != "stop"
                or choices[0]["message"]["role"] != "assistant"
                or len(choices[0]["message"]["content"]) != 1
            ):
                raise ValueError("one_complete_generated_image_required")
            url = download_url(choices[0]["message"]["content"][0]["image"])
            raw_path = self.folder / "generated.png.body"
            generated_raw = (
                self.cached("generated.png", MAX_DOWNLOAD, "image/png")
                if raw_path.exists()
                else self.exchange(client, "GET", url, "generated.png", image=True)
            )
            generated, generated_meta = asyncio.run(decode(generated_raw, "image/png"))
            save(self.folder / "generated.png", generated, replay=True)
            self.report["real_model_output_exists"] = self.live
            if (generated_meta["width"], generated_meta["height"]) != (
                meta["width"],
                meta["height"],
            ):
                raise ValueError("generated_dimensions_do_not_match_pixel_exact_scope")
            metering = image_usage(
                result.get("usage"), input_count=1, width=meta["width"], height=meta["height"]
            )
            self.report["generation_metering"] = metering
            if metering["quantities"] is None:
                raise ValueError("generation_usage_unconfirmed_no_followup_detection")
            estimated = sum(
                Decimal(prices[MODEL][name]) * value
                for name, value in metering["quantities"].items()
            )
            self.report["generation_reference_estimate_cny"] = amount(estimated)
            if estimated > Decimal(self.scope.generate_ceiling):
                raise ValueError("generation_reference_cost_above_ceiling")
            final, _ = asyncio.run(transform(source, generated, generation))
            _, protection = asyncio.run(transform(source, final, generation, verify=True))
            save(self.folder / "final.png", final, replay=True)
            save(self.folder / "pixel-protection.json", encoded(protection), replay=True)
            if len(final) > 10_000_000:
                raise ValueError("final_png_exceeds_reviewed_detection_input_limit")
            vision_request = {
                "model": VISION_MODEL,
                "stream": False,
                "enable_thinking": False,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": VISION_SYSTEM},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/png;base64,"
                                    + base64.b64encode(final).decode()
                                },
                            },
                            {
                                "type": "text",
                                "text": f"Inspect this final image, {meta['width']} by {meta['height']} pixels. Return JSON visual facts only.",
                            },
                        ],
                    },
                ],
            }
            detection_raw = self.post_once(client, "detection", VISION_PATH, vision_request)
            facts, vision_metering, response_id, integrity = parse_visual_response(
                detection_raw, meta["width"], meta["height"]
            )
            save(
                self.folder / "detection-facts.json",
                encoded(
                    {
                        "facts": facts.model_dump(mode="json"),
                        **vision_metering,
                        "response_id": response_id,
                        "structure_integrity": integrity,
                        "final_sha256": sha(final),
                        "wire_response_sha256": sha(detection_raw),
                        "transport": self.report["transport"],
                    }
                ),
                replay=True,
            )
            self.report.update(
                result="SINGLE_ROUND_COMPLETED",
                final_sha256=sha(final),
                pixel_exact_passed=protection["pixel_exact_passed"],
                detection_structure_integrity=integrity,
                detection_analysis_complete=facts.analysis_complete,
                detection_facts_schema_verified="qwen_facts_schema_or_response_unverified"
                not in facts.uncertainties
                and integrity == "valid",
                detection_metering=vision_metering,
                manual_review_required=True,
            )
            if vision_metering["quantities"] is not None:
                counts = vision_metering["quantities"]
                cost = (
                    Decimal(counts["input_tokens"]) * Decimal(prices[VISION_MODEL]["input_token"])
                    + Decimal(counts["cached_input_tokens"])
                    * Decimal(prices[VISION_MODEL]["input_token_cache"])
                    + Decimal(counts["output_tokens"])
                    * Decimal(prices[VISION_MODEL]["output_token"])
                ) / 1000000
                self.report["detection_reference_estimate_cny"] = amount(cost)
                self.report["total_reference_estimate_cny"] = amount(estimated + cost)
            local = {"width": meta["width"], "height": meta["height"], "byte_size": len(final)}
            rules = [
                {
                    "platform": "amazon",
                    "marketplace": "US",
                    "category": "electronics",
                    "image_type": "MAIN",
                    "version": "partial-size-draft-only",
                    "evidence_status": "unverified",
                    "config": {
                        "min_longest_edge": 500,
                        "max_longest_edge": 10000,
                        "complete_rule_set": False,
                        "require_identity_review": True,
                    },
                }
            ]
            save(
                self.folder / "compliance-draft.json",
                encoded(evaluate_rules(sha(final), rules, facts.model_dump(mode="json"), local)),
                replay=True,
            )
        if (
            sha(bounded_file(self.scope.source_path, 10_000_000)) != self.scope.source_sha256
            or sha(bounded_file(self.scope.original_path, MAX_DOWNLOAD))
            != self.scope.original_sha256
        ):
            raise ValueError("source_or_original_changed_during_smoke")
        self.report["original_and_reference_unchanged"] = True
        return self.report
