import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import httpx
from qwen_fixture import canonical_png

from visual_platform.providers.appearance_understanding import understand
from visual_platform.providers.bfl import BflRequest
from visual_platform.providers.contract import ProviderFailure, ProviderUncertain

SUMMARY = {
    "description": "圆角黑色盒体，前面可见一处标识。侧面接口被遮挡，未知。",
    "colors": ["黑色盒体"],
    "materials": [],
    "uncertainties": ["侧面接口不可见"],
}


class AppearanceUnderstandingContracts(unittest.IsolatedAsyncioTestCase):
    async def test_summary_is_source_grounded_archived_and_reaches_redraw_without_pixels(self):
        calls = []
        raw = json.dumps(
            {
                "model": "qwen3.8-flash",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(SUMMARY)},
                    }
                ],
            }
        ).encode()

        def route(request):
            calls.append(request)
            payload = json.loads(request.content)
            self.assertIn("商品本体", payload["messages"][0]["content"])
            self.assertFalse(payload["enable_thinking"])
            self.assertIn(
                "data:image/png;base64,", payload["messages"][1]["content"][0]["image_url"]["url"]
            )
            return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

        with tempfile.TemporaryDirectory() as folder:
            identity = uuid4()
            with self.assertRaises(ValueError):
                await understand(
                    identity,
                    canonical_png(),
                    key="synthetic",
                    origin="https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
                    directory=Path(folder),
                )
            self.assertEqual(list(Path(folder).iterdir()), [])
            summary, digest, metering = await understand(
                identity,
                canonical_png(),
                key="synthetic",
                origin="https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
                directory=Path(folder),
                transport=httpx.MockTransport(route),
            )
            self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
            self.assertEqual((Path(folder) / (str(identity) + ".response.body")).read_bytes(), raw)
            self.assertIsNone(metering["quantities"])
            with self.assertRaises(ProviderUncertain):
                await understand(
                    identity,
                    canonical_png(),
                    key="synthetic",
                    origin="https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
                    directory=Path(folder),
                    transport=httpx.MockTransport(route),
                )
            self.assertEqual(len(calls), 1)
            config = summary.config().model_dump()
            config.update(source_asset_version_id=str(uuid4()), understanding_id=str(identity))
            redraw = BflRequest.from_canonical(
                {
                    "type": "scene",
                    "prompt": "柔和室内光线",
                    "output": {"width": 512, "height": 512, "format": "png"},
                    "protection_mode": "none",
                    "regions": [],
                },
                canonical_png(),
                {"config": config},
            )
            self.assertIn(SUMMARY["description"], redraw.prompt)
            self.assertIn("Product appearance lock", redraw.prompt)
            self.assertNotIn(str(identity), redraw.prompt)

    async def test_timeout_truncation_array_and_wrong_model_never_resubmit_or_fabricate_summary(
        self,
    ):
        for fault in ("timeout", "length", "array", "wrong_model"):
            calls = []

            def route(request):
                calls.append(request)
                if fault == "timeout":
                    raise httpx.ReadTimeout("private wire text")
                return httpx.Response(
                    200,
                    json={
                        "model": "wrong" if fault == "wrong_model" else "qwen3.8-flash",
                        "choices": [
                            {
                                "finish_reason": "length" if fault == "length" else "stop",
                                "message": {
                                    "role": "assistant",
                                    "content": "[]" if fault == "array" else json.dumps(SUMMARY),
                                },
                            }
                        ],
                    },
                )

            with tempfile.TemporaryDirectory() as folder:
                identity = uuid4()
                for _ in range(2):
                    with self.assertRaises(ProviderUncertain):
                        await understand(
                            identity,
                            canonical_png(),
                            key="synthetic",
                            origin="https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
                            directory=Path(folder),
                            transport=httpx.MockTransport(route),
                        )
                self.assertEqual(len(calls), 1)

    async def test_explicit_rejection_retains_original_body(self):
        with tempfile.TemporaryDirectory() as folder:
            identity = uuid4()
            with self.assertRaises(ProviderFailure):
                await understand(
                    identity,
                    canonical_png(),
                    key="synthetic",
                    origin="https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
                    directory=Path(folder),
                    transport=httpx.MockTransport(
                        lambda _: httpx.Response(403, json={"error": {"code": "access_denied"}})
                    ),
                )
            self.assertIn(
                b"access_denied", (Path(folder) / (str(identity) + ".response.body")).read_bytes()
            )
