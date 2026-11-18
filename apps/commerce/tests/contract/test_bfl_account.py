"""Synthetic read-only BFL account responses; never use a real account key."""

import unittest

import httpx

from visual_platform.providers.bfl_account import (
    ACCOUNT_ENDPOINT,
    AccountProbeUnavailable,
    probe_bfl_credits,
)


class BflAccountContracts(unittest.TestCase):
    def test_exact_authenticated_get_preserves_decimal_and_raw_digest_without_model_calls(self):
        calls = []

        def route(request):
            calls.append(request)
            return httpx.Response(
                200,
                content=b'{"credits":123.450000001}',
                headers={"content-type": "application/json"},
            )

        receipt, raw = probe_bfl_credits(
            "synthetic-private-key", transport=httpx.MockTransport(route)
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0].method, str(calls[0].url)), ("GET", ACCOUNT_ENDPOINT))
        self.assertEqual(calls[0].headers["x-key"], "synthetic-private-key")
        self.assertEqual(receipt["credit_balance"], "123.450000001")
        self.assertEqual(raw, b'{"credits":123.450000001}')
        self.assertFalse(receipt["models_verified"])
        self.assertFalse(receipt["spending_authorized"])
        self.assertEqual(receipt["paid_model_calls"], 0)
        self.assertNotIn("synthetic-private-key", str(receipt))
        self.assertEqual(receipt["transport"], "offline_mock")

    def test_redirect_rate_limit_auth_and_server_error_never_retry_or_follow(self):
        for status in (302, 401, 403, 429, 503):
            calls = []

            def route(request):
                calls.append(request)
                return httpx.Response(
                    status,
                    headers={"location": "https://untrusted.example"},
                    text="private error details",
                )

            with self.subTest(status=status), self.assertRaises(AccountProbeUnavailable) as failure:
                probe_bfl_credits("synthetic-key", transport=httpx.MockTransport(route))
            self.assertEqual(len(calls), 1)
            self.assertNotIn("private error details", str(failure.exception))

    def test_duplicate_boolean_negative_nonfinite_and_unbounded_numbers_reject(self):
        for raw in (
            b'{"credits":1,"credits":2}',
            b'{"credits":true}',
            b'{"credits":-1}',
            b'{"credits":NaN}',
            b'{"credits":Infinity}',
            b'{"credits":1e1000000}',
            b'{"credits":1e-1000000}',
            b'{"credits":"1"}',
            b"{}",
        ):
            with self.subTest(raw=raw), self.assertRaises(AccountProbeUnavailable):
                probe_bfl_credits(
                    "synthetic-key",
                    transport=httpx.MockTransport(
                        lambda _: httpx.Response(
                            200, content=raw, headers={"content-type": "application/json"}
                        )
                    ),
                )
        receipt, _ = probe_bfl_credits(
            "synthetic-key",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"credits": 0})),
        )
        self.assertFalse(receipt["positive_balance"])

    def test_oversized_or_compressed_response_rejects_and_timeout_is_generic(self):
        for response in (
            httpx.Response(200, content=b" " * 65537, headers={"content-type": "application/json"}),
            httpx.Response(
                200,
                content=b"",
                headers={"content-type": "application/json", "content-encoding": "gzip"},
            ),
        ):
            with self.assertRaises(AccountProbeUnavailable):
                probe_bfl_credits(
                    "synthetic-key", transport=httpx.MockTransport(lambda _: response)
                )

        def timeout(request):
            raise httpx.ReadTimeout("private transport details", request=request)

        with self.assertRaises(AccountProbeUnavailable) as failure:
            probe_bfl_credits("synthetic-key", transport=httpx.MockTransport(timeout))
        self.assertNotIn("private transport details", str(failure.exception))
