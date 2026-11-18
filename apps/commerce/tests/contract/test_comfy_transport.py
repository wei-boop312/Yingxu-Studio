import hashlib
import json
import unittest
from uuid import uuid4

import httpx

from visual_platform.providers.comfyui import (
    ComfyUnavailable,
    ComfyUncertain,
    LocalComfy,
    local_url,
)


class ComfyTransportTests(unittest.IsolatedAsyncioTestCase):
    def test_only_explicit_loopback_is_accepted(self):
        self.assertEqual(local_url("http://127.0.0.1:8188/"), "http://127.0.0.1:8188")
        for value in (
            "http://example.com:8188",
            "http://localhost:8188",
            "http://127.0.0.1:8188/path",
            "http://user:pass@127.0.0.1:8188",
            "http://127.0.0.1:8188?token=x",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                local_url(value)

    async def test_diagnostics_are_read_only_and_strip_private_argv(self):
        calls = []

        def respond(request):
            calls.append(request.method)
            return httpx.Response(
                200,
                json={"system": {"comfyui_version": "0.43.0", "argv": ["private-path"]}}
                if request.url.path == "/system_stats"
                else {name: {} for name in ("LoadImage", "ImageScale", "SaveImage")},
            )

        client = LocalComfy("http://127.0.0.1:8188", httpx.MockTransport(respond))
        try:
            summary, _ = await client.diagnose()
            self.assertEqual(calls, ["GET", "GET"])
            self.assertNotIn("private-path", json.dumps(summary))
            self.assertFalse(summary["generation_template_verified"])
            self.assertEqual(summary["model_byte_identity"], "unknown")
        finally:
            await client.close()

    async def test_lost_submission_response_is_never_retried(self):
        calls = []

        def respond(request):
            calls.append(request.method)
            raise httpx.ReadTimeout("response lost", request=request)

        client = LocalComfy("http://127.0.0.1:8188", httpx.MockTransport(respond))
        try:
            with self.assertRaises(ComfyUncertain):
                await client.submit_once({"1": {}}, uuid4(), uuid4())
            self.assertEqual(calls, ["POST"])
        finally:
            await client.close()

    async def test_redirects_and_foreign_prompt_ids_are_uncertain(self):
        for response in (
            httpx.Response(302, headers={"location": "https://example.invalid"}),
            httpx.Response(200, json={"prompt_id": str(uuid4())}),
            httpx.Response(503),
        ):
            calls = []

            def respond(request):
                calls.append(str(request.url))
                return response

            client = LocalComfy("http://127.0.0.1:8188", httpx.MockTransport(respond))
            try:
                with self.assertRaises(ComfyUncertain):
                    await client.submit_once({"1": {}}, uuid4(), uuid4())
                self.assertEqual(len(calls), 1)
            finally:
                await client.close()

    async def test_exact_input_readback_is_required(self):
        raw = b"canonical PNG test bytes"
        sha = hashlib.sha256(raw).hexdigest()
        intent = uuid4()
        for changed in (False, True):
            calls = []

            def respond(request):
                calls.append(request.method)
                if calls == ["GET"]:
                    return httpx.Response(404)
                return (
                    httpx.Response(
                        200,
                        json={
                            "name": sha + ".png",
                            "type": "input",
                            "subfolder": "yingxu/" + str(intent),
                        },
                    )
                    if request.method == "POST"
                    else httpx.Response(200, content=b"changed" if changed else raw)
                )

            client = LocalComfy("http://127.0.0.1:8188", httpx.MockTransport(respond))
            try:
                if changed:
                    with self.assertRaises(ComfyUnavailable):
                        await client.upload_exact(raw, sha, intent)
                else:
                    self.assertEqual(
                        await client.upload_exact(raw, sha, intent),
                        "yingxu/" + str(intent) + "/" + sha + ".png",
                    )
                self.assertEqual(calls, ["GET", "POST", "GET"])
            finally:
                await client.close()

    async def test_cancellation_never_uses_global_queue_or_interrupt(self):
        own, other = uuid4(), uuid4()
        for running in (True, False):
            calls = []
            deleted = []

            def respond(request):
                calls.append(
                    (request.url.path, json.loads(request.content) if request.content else None)
                )
                if request.url.path.startswith("/history/"):
                    return httpx.Response(200, json={})
                if request.method == "GET":
                    return httpx.Response(
                        200,
                        json={
                            "queue_running": [[0, str(own)]] if running else [],
                            "queue_pending": [[1, str(other)]]
                            + ([] if deleted else [[2, str(own)]]),
                        },
                    )
                deleted.append(True)
                return httpx.Response(200)

            client = LocalComfy("http://127.0.0.1:8188", httpx.MockTransport(respond))
            try:
                result = await client.cancel_pending(own)
                self.assertEqual(result["cancelled"], not running)
                self.assertEqual(
                    calls,
                    [("/queue", None)]
                    if running
                    else [
                        ("/queue", None),
                        ("/queue", {"delete": [str(own)]}),
                        ("/queue", None),
                        ("/history/" + str(own), None),
                    ],
                )
            finally:
                await client.close()
