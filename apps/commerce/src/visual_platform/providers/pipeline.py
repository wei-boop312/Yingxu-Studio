"""Protocol composition, enabled only with explicit offline HTTP mock transports."""

import asyncio
import copy
import hashlib
import re
import struct
from uuid import UUID

from visual_platform.providers.bfl import BflProvider, BflRequest
from visual_platform.providers.contract import ProviderPending, ProviderUncertain, Receipt
from visual_platform.providers.gemini import MODEL, OFFICIAL_ORIGIN, validate_archive_record
from visual_platform.providers.generation_archive import (
    generation_observation,
    validate_generation_archive,
)

BASELINE_ENDPOINTS = {
    "generate": BflProvider.endpoint,
    "detect": OFFICIAL_ORIGIN + "/v1beta/models/" + MODEL + ":generateContent",
}


class ProtocolProvider:
    allow_live_transport = False
    generation_model = "flux-3-image"
    detection_model = MODEL
    request_type = BflRequest
    execution_profile = "offline_protocol_mock"
    report_profile = "offline_protocol_mock"
    durable_detection_archive = True
    durable_generation_archive = True

    def __init__(self, bfl, gemini, *, merchant_accounts=None, runtime_sha256=None):
        if (
            not bfl.offline_transport or not gemini.offline_transport
        ) and not self.allow_live_transport:
            raise ValueError(
                "protocol pipeline requires explicit offline MockTransport on both suppliers"
            )
        if merchant_accounts is not None:
            if (
                type(merchant_accounts) is not dict
                or not merchant_accounts
                or not set(merchant_accounts) <= {"generate", "detect"}
            ):
                raise ValueError("invalid merchant account scope")
            for account in merchant_accounts.values():
                if (
                    type(account) is not dict
                    or set(account) != {"merchant_fingerprint", "billing_group"}
                    or type(account["merchant_fingerprint"]) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}", account["merchant_fingerprint"])
                    or type(account["billing_group"]) is not str
                    or not 1 <= len(account["billing_group"]) <= 160
                    or not account["billing_group"].strip()
                    or any(ord(char) < 32 for char in account["billing_group"])
                ):
                    raise ValueError("invalid merchant account scope")
        if runtime_sha256 is not None and (
            type(runtime_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", runtime_sha256)
        ):
            raise ValueError("invalid_runtime_configuration_digest")
        self.runtime_sha256 = runtime_sha256
        self.merchant_accounts = copy.deepcopy(merchant_accounts)
        self.bfl = bfl
        self.gemini = gemini

    @property
    def request_endpoints(self):
        return {
            "generate": self.bfl.endpoint,
            "detect": (
                self.gemini.endpoint_origin + "/v1beta/models/" + MODEL + ":generateContent"
                if self.detection_model == MODEL
                else self.gemini.endpoint
            ),
        }

    def verify_execution_scope(self, execution):
        expected_models = (
            {"generate": self.generation_model, "detect": self.detection_model}
            if self.runtime_sha256
            else None
        )
        if (
            execution.get("provider_runtime_sha256") != self.runtime_sha256
            or execution.get("provider_models") != expected_models
        ):
            raise ValueError("locked_provider_runtime_mismatch")
        if execution.get("provider_accounts") != self.merchant_accounts:
            raise ValueError("locked_provider_account_mismatch")
        # Old mock quotes always meant the official endpoints. A new gateway is
        # never inferred from environment settings or a matching model name.
        if execution.get("provider_endpoints", BASELINE_ENDPOINTS) != self.request_endpoints:
            raise ValueError("locked_provider_endpoint_mismatch")

    def prepare_generate(self, generation, source, appearance=None):
        return self.request_type.from_canonical(generation, source, appearance).model_dump()

    async def submit_generate(self, intent, payload):
        return await asyncio.to_thread(
            self.bfl.submit, intent, self.request_type.model_validate(payload)
        )

    async def retrieve_image(self, attempt):
        receipt = Receipt(
            attempt["provider_id"], attempt["response_digest"], attempt["polling_url_private"]
        )
        poll = await asyncio.to_thread(self.bfl.poll, receipt)
        if poll.status != "Ready":
            raise ProviderPending("existing BFL result pending")
        return await asyncio.to_thread(self.bfl.fetch_result, poll)

    async def submit_detect(self, intent, final):
        if len(final) < 24 or not final.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("final PNG required")
        width, height = struct.unpack(">II", final[16:24])
        return await self.gemini.submit(
            intent,
            final,
            expected_sha256=hashlib.sha256(final).hexdigest(),
            width=width,
            height=height,
        )

    def fetch_facts(self, identity):
        return self.gemini.fetch_facts(identity)

    def fetch_generation_record(self, intent):
        receipt = self.bfl.receipt_record(intent)
        if receipt.get("archive_version") != 2:
            return None
        return validate_generation_archive(
            {
                "archive_version": 1,
                "provider": "bfl",
                "model": self.generation_model,
                "endpoint_origin": "https://api.bfl.ai",
                "provider_id": receipt["provider_id"],
                "polling_url": receipt["polling_url"],
                "response_digest": receipt["response_digest"],
                "wire_response_sha256": receipt["wire_response_sha256"],
                "response_body_base64": receipt["response_body_base64"],
            }
        )

    validate_generation_record = staticmethod(validate_generation_archive)
    observation_from_generation = staticmethod(generation_observation)

    def observation(self, step, attempt):
        if step == "generate":
            try:
                record = self.bfl.receipt_record(UUID(attempt["id"]))
            except ProviderUncertain:
                return {
                    "transport": "offline_mock",
                    "response_digest": attempt["response_digest"],
                    "provider_cost": None,
                    "source": "known_db_receipt_private_cost_metadata_unavailable",
                }
            if record.get("archive_version") != 2:
                return {
                    "transport": "offline_mock",
                    "response_digest": attempt["response_digest"],
                    "provider_cost": None,
                    "source": "legacy_local_receipt_has_no_wire_evidence",
                }
            if (
                record["provider_id"] != attempt["provider_id"]
                or record["response_digest"] != attempt["response_digest"]
            ):
                raise ProviderUncertain("BFL receipt does not match the frozen attempt")
            return {
                "transport": "offline_mock",
                "response_digest": record["response_digest"],
                "provider_cost": record["cost"],
            }
        record = self.gemini.fetch_facts(attempt["provider_id"])
        return self.observation_from_facts(record)

    def validate_facts_record(self, record):
        return validate_archive_record(record)

    @staticmethod
    def observation_from_facts(record):
        return {
            "transport": "offline_mock",
            "response_digest": record["response_digest"],
            "usage": record["usage"],
            "billing_usage_integrity": record.get("billing_usage_integrity", "missing"),
            "reported_model_version": record.get("reported_model_version"),
            "cost": record["cost"],
            "final_sha256": record["final_sha256"],
            "sdk_version": record["sdk_version"],
            "archive_version": record.get("archive_version", 1),
            "wire_response_sha256": record.get("wire_response_sha256"),
            "response_structure_integrity": record.get("response_structure_integrity"),
        }
