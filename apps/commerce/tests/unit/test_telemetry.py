import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from visual_platform.services.telemetry import RequestTelemetry, archive_observed
from visual_platform.services.telemetry_reader import parse_observation, summarize_telemetry


class RequestTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def observe(self, app, path="/api/v1/products"):
        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            pass

        with patch("visual_platform.services.telemetry.emit_observation") as emit:
            await RequestTelemetry(app)({"type": "http", "path": path}, receive, send)
            return emit.call_args

    async def test_chunked_response_measures_body_completion_without_recording_private_input(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 503})
            await send({"type": "http.response.body", "body": b"private body", "more_body": True})
            await asyncio.sleep(0.002)
            await send({"type": "http.response.body", "body": b"private end"})

        call = await self.observe(app, "/api/v1/products/private-id")
        self.assertEqual(call.args, ("api_request_observed",))
        self.assertEqual(call.kwargs["status"], 503)
        self.assertEqual(call.kwargs["outcome"], "completed")
        self.assertGreater(call.kwargs["duration_ms"], 0)
        self.assertNotIn("private", str(call))

    async def test_application_exception_is_observed_and_original_error_propagates(self):
        async def app(*args):
            raise RuntimeError("private error")

        with patch("visual_platform.services.telemetry.emit_observation") as emit:

            async def noop(*args):
                pass

            with self.assertRaisesRegex(RuntimeError, "private error"):
                await RequestTelemetry(app)(
                    {"type": "http", "path": "/api/v1/products"}, noop, noop
                )
            self.assertEqual(emit.call_args.kwargs["status"], 500)
            self.assertEqual(emit.call_args.kwargs["outcome"], "exception")
            self.assertNotIn("private", str(emit.call_args))

    async def test_health_and_sse_are_excluded_and_network_interruption_is_not_a_5xx_sample(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})

        for path in ("/api/v1/health/ready", "/api/v1/tasks/private-id/events"):
            self.assertIsNone(await self.observe(app, path))

        async def receive():
            pass

        async def broken_send(message):
            raise ConnectionError("private socket")

        with patch("visual_platform.services.telemetry.emit_observation") as emit:
            with self.assertRaises(ConnectionError):
                await RequestTelemetry(app)(
                    {"type": "http", "path": "/api/v1/products"}, receive, broken_send
                )
            self.assertEqual(emit.call_args.kwargs["outcome"], "interrupted")


class TelemetryReaderTests(unittest.TestCase):
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)

    def observation(self, event, **fields):
        return {
            "event": event,
            "observation_id": str(uuid4()),
            "observed_at": self.now.isoformat(),
            "duration_ms": 1,
            **fields,
        }

    def test_optional_server_request_id_is_validated_without_changing_legacy_schema(self):
        old = self.observation("api_request_observed", status=503, outcome="completed")
        identity = str(uuid4())
        self.assertEqual(parse_observation(old), self.now)
        self.assertEqual(parse_observation({**old, "request_id": identity}), self.now)
        for bad in (None, True, 42, "untrusted-client-value", "https://private.invalid"):
            with self.subTest(bad=type(bad).__name__), self.assertRaises(ValueError):
                parse_observation({**old, "request_id": bad})
        with self.assertRaises(ValueError):
            parse_observation(
                self.observation("storage_archive_observed", outcome="success", request_id=identity)
            )

    def test_exact_p95_duplicates_errors_and_archive_failures(self):
        api = [
            self.observation(
                "api_request_observed",
                duration_ms=n,
                status=500 if n == 100 else 200,
                outcome="completed",
            )
            for n in range(1, 101)
        ]
        archive = [
            self.observation("storage_archive_observed", outcome=value)
            for value in ("success", "failure")
        ]
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "first.log"
            second = Path(folder) / "copy.log"
            first.write_text("\n".join(json.dumps(v) for v in api + archive))
            second.write_bytes(first.read_bytes())
            result = summarize_telemetry([first, second], [first, second], now=self.now)
        self.assertEqual(
            (result["api_samples"], result["api_p95_ms"], result["api_5xx_count"]), (100, 95, 1)
        )
        self.assertEqual((result["archive_operations"], result["archive_failure_count"]), (2, 1))
        self.assertEqual(result["duplicate_observations"], 102)
        self.assertTrue(result["coverage_complete_for_supplied_files"])

    def test_missing_samples_are_unknown_and_malformed_or_truncated_logs_cannot_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "log"
            path.write_text("private non-telemetry line\n")
            empty = summarize_telemetry([path], [path], now=self.now)
            for key in ("api_p95_ms", "api_5xx_count", "archive_failure_count"):
                self.assertIsNone(empty[key])
            invalid = self.observation(
                "api_request_observed", status=200, outcome="completed", password="private"
            )
            path.write_text("127.0.0.1 GET /" + json.dumps(invalid) + " HTTP/1.1 404\n")
            self.assertEqual(summarize_telemetry([path], now=self.now)["malformed_observations"], 0)
            path.write_text(json.dumps(invalid))
            bad = summarize_telemetry([path], now=self.now)
            self.assertFalse(bad["coverage_complete_for_supplied_files"])
            self.assertNotIn("private", json.dumps(bad))
            path.write_text(
                json.dumps(
                    self.observation("api_request_observed", status=200, outcome="completed")
                )
            )
            self.assertTrue(
                summarize_telemetry([path], now=self.now, max_bytes=10)["inputs_truncated"]
            )

    def test_record_limit_conflicting_ids_and_nonfinite_duration_are_rejected(self):
        one = self.observation("api_request_observed", status=200, outcome="completed")
        two = {**one, "status": 500}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "log"
            path.write_text("\n".join(json.dumps(v) for v in (one, two)))
            self.assertEqual(summarize_telemetry([path], now=self.now)["malformed_observations"], 1)
            path.write_text(
                "\n".join(
                    json.dumps(
                        self.observation("api_request_observed", status=200, outcome="completed")
                    )
                    for _ in range(2)
                )
            )
            self.assertTrue(
                summarize_telemetry([path], now=self.now, max_records=1)["inputs_truncated"]
            )
            one["duration_ms"] = float("nan")
            path.write_text(json.dumps(one))
            self.assertFalse(
                summarize_telemetry([path], now=self.now)["coverage_complete_for_supplied_files"]
            )

    def test_archive_wrapper_preserves_success_and_exception_without_exposing_keys(self):
        @archive_observed
        def write(key):
            if key == "private failure":
                raise ValueError("private failure details")
            return "version"

        with patch("visual_platform.services.telemetry.emit_observation") as emit:
            self.assertEqual(write("private object key"), "version")
            with self.assertRaisesRegex(ValueError, "private failure details"):
                write("private failure")
            self.assertEqual(
                [call.kwargs["outcome"] for call in emit.call_args_list], ["success", "failure"]
            )
            self.assertNotIn("private", str(emit.call_args_list))
