"""Synthetic terminal query evidence with separate raw-download and canonical image hashes."""

import base64
import copy
import hashlib
import json
import unittest
from io import BytesIO

from PIL import Image, PngImagePlugin

from visual_platform.imaging.normalize import normalize
from visual_platform.providers.qwen_image import QwenImageArtifact
from visual_platform.providers.qwen_result import (
    build_qwen_result,
    qwen_result_observation,
    validate_qwen_result,
)

ORIGIN = "https://ws-synthetic.cn-beijing.maas.aliyuncs.com"


class QwenResultContracts(unittest.TestCase):
    def setUp(self):
        stream = BytesIO()
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", "synthetic metadata; must not enter canonical PNG")
        Image.new("RGB", (512, 512), "white").save(stream, "PNG", pnginfo=info)
        self.download = stream.getvalue()
        self.canonical = normalize(self.download)
        self.body = {
            "output": {
                "task_id": "synthetic",
                "task_status": "SUCCEEDED",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "image": "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/synthetic.png?Expires=1"
                                }
                            ],
                        },
                    }
                ],
            },
            "usage": {
                "output_width": 512,
                "output_height": 512,
                "input_image_count": 1,
                "output_image_count": 1,
                "input_image_type": "qima_input_1k",
                "output_image_type": "qima_output_1k",
            },
        }

    def archive(self, body=None):
        raw = json.dumps(self.body if body is None else body).encode()
        artifact = QwenImageArtifact(
            self.download,
            "image/png",
            hashlib.sha256(self.download).hexdigest(),
            base64.b64encode(raw).decode(),
            hashlib.sha256(raw).hexdigest(),
        )
        return build_qwen_result(
            {"provider_id": "synthetic", "response_digest": "a" * 64},
            artifact,
            self.canonical,
            {"width": 512, "height": 512},
            ORIGIN,
        )

    def test_terminal_usage_has_original_wire_and_distinct_download_canonical_digests(self):
        record = self.archive()
        self.assertNotEqual(record["downloaded_sha256"], record["generated_sha256"])
        self.assertEqual(record["generated_sha256"], hashlib.sha256(self.canonical).hexdigest())
        self.assertEqual(record["submission_response_sha256"], "a" * 64)
        self.assertEqual(record["billing_usage_integrity"], "complete")
        self.assertEqual(
            record["quantities"],
            {"qima_input_1k": 1, "qima_input_2k": 0, "qima_output_1k": 1, "qima_output_2k": 0},
        )
        observation = qwen_result_observation(record)
        self.assertFalse(observation["actual_spending_verified"])
        self.assertNotIn("currency", observation)
        self.assertNotIn("amount", observation)

    def test_missing_partial_or_ambiguous_usage_is_unknown_while_original_bytes_remain(self):
        for usage in (
            None,
            {},
            {**self.body["usage"], "input_image_count": True},
            {**self.body["usage"], "output_image_count": 1.0},
        ):
            body = copy.deepcopy(self.body)
            body["usage"] = usage
            record = self.archive(body)
            self.assertIsNone(record["quantities"])
            self.assertNotEqual(record["billing_usage_integrity"], "complete")
            self.assertEqual(json.loads(base64.b64decode(record["response_body_base64"])), body)

    def test_wire_identity_dimensions_quantities_and_secret_injection_cannot_be_rebound(self):
        for change in (
            {"provider_id": "different"},
            {"wire_response_sha256": "f" * 64},
            {"width": 513},
            {"input_image_count": True},
            {"api_key": "synthetic"},
            {
                "quantities": {
                    "qima_input_1k": True,
                    "qima_input_2k": 0,
                    "qima_output_1k": 1,
                    "qima_output_2k": 0,
                }
            },
        ):
            with (
                self.subTest(change=change),
                self.assertRaisesRegex(ValueError, "invalid_qwen_result_archive"),
            ):
                validate_qwen_result({**self.archive(), **change})

    def test_duplicate_json_body_rejected_even_if_digest_is_recomputed(self):
        record = self.archive()
        raw = (
            json.dumps(self.body)
            .replace('"task_id": "synthetic"', '"task_id":"other","task_id":"synthetic"')
            .encode()
        )
        record.update(
            response_body_base64=base64.b64encode(raw).decode(),
            wire_response_sha256=hashlib.sha256(raw).hexdigest(),
        )
        with self.assertRaises(ValueError):
            validate_qwen_result(record)
