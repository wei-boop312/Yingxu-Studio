"""Exact original-wire submission evidence, separate from result billing usage."""

import base64
import copy
import hashlib
import unittest

from visual_platform.providers.generation_archive import (
    generation_observation,
    validate_generation_archive,
)


def generation_record(provider="bfl"):
    bfl = provider == "bfl"
    raw = (
        b'{ "id":"synthetic", "polling_url":"https://api.eu2.bfl.ai/v1/get_result?id=synthetic", "cost":0.0600000000000000001 }'
        if bfl
        else b'{"output":{"task_id":"synthetic","task_status":"PENDING"}}'
    )
    digest = hashlib.sha256(raw).hexdigest()
    origin = "https://api.bfl.ai" if bfl else "https://ws-synthetic.cn-beijing.maas.aliyuncs.com"
    return {
        "archive_version": 1,
        "provider": provider,
        "model": "flux-3-image" if bfl else "qwen-image-3.0",
        "endpoint_origin": origin,
        "provider_id": "synthetic",
        "polling_url": "https://api.eu2.bfl.ai/v1/get_result?id=synthetic"
        if bfl
        else origin + "/api/v1/tasks/synthetic",
        "response_digest": digest,
        "wire_response_sha256": digest,
        "response_body_base64": base64.b64encode(raw).decode(),
    }


class GenerationArchiveContracts(unittest.TestCase):
    def test_bfl_exact_credit_decimal_is_derived_again_from_original_bytes(self):
        record = generation_record()
        self.assertEqual(validate_generation_archive(record), record)
        observation = generation_observation(record)
        self.assertEqual(observation["provider_cost"]["quantity"], "0.0600000000000000001")
        self.assertEqual(
            observation["provider_cost"]["source_digest"], record["wire_response_sha256"]
        )
        self.assertIsNone(observation["provider_cost"]["currency"])

    def test_qwen_async_receipt_does_not_invent_usage_or_bfl_credits(self):
        observation = generation_observation(generation_record("aliyun_bailian"))
        self.assertIsNone(observation["provider_cost"])
        self.assertNotIn("usage", observation)
        self.assertNotIn("bfl_credit", str(observation))

    def test_mutated_identity_digest_origin_and_extra_fields_rejected(self):
        for change in ("identity", "digest", "origin", "extra", "version", "oversize"):
            record = copy.deepcopy(generation_record())
            if change == "identity":
                record["provider_id"] = "other"
            elif change == "digest":
                record["response_digest"] = "a" * 64
            elif change == "origin":
                record["endpoint_origin"] += ".evil.invalid"
            elif change == "extra":
                record["api_key"] = "synthetic"
            elif change == "version":
                record["archive_version"] = True
            else:
                record["response_body_base64"] = "A" * 1398108
            with (
                self.subTest(change=change),
                self.assertRaisesRegex(ValueError, "invalid_generation_wire_archive"),
            ):
                validate_generation_archive(record)

    def test_duplicate_body_keys_do_not_become_evidence_even_with_valid_sha(self):
        record = generation_record("aliyun_bailian")
        raw = b'{"output":{"task_id":"other","task_id":"synthetic","task_status":"PENDING"}}'
        record.update(
            response_body_base64=base64.b64encode(raw).decode(),
            response_digest=hashlib.sha256(raw).hexdigest(),
            wire_response_sha256=hashlib.sha256(raw).hexdigest(),
        )
        with self.assertRaises(ValueError):
            validate_generation_archive(record)
