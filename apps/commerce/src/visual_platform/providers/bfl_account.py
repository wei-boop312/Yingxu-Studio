"""One exact read-only BFL credits GET; no generation methods or automatic retries."""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from visual_platform.providers.gemini_usage import unique_object
from visual_platform.providers.runtime import credential_fingerprint

ACCOUNT_ENDPOINT = "https://api.bfl.ai/v1/credits"
MAX_ACCOUNT_BODY = 64 * 1024


class AccountProbeUnavailable(Exception):
    pass


def probe_bfl_credits(key, *, transport=None):
    fingerprint = credential_fingerprint(key)

    def invalid(value):
        raise ValueError("nonstandard_account_json")

    try:
        with httpx.Client(
            transport=transport or httpx.HTTPTransport(retries=0),
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(15, connect=5),
        ) as client:
            with client.stream(
                "GET",
                ACCOUNT_ENDPOINT,
                headers={"x-key": key, "accept": "application/json", "accept-encoding": "identity"},
            ) as response:
                if (
                    response.status_code != 200
                    or response.headers.get("content-encoding", "identity").lower() != "identity"
                    or response.headers.get("content-type", "").split(";", 1)[0].lower()
                    != "application/json"
                ):
                    raise ValueError("account_response_unavailable")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=4096):
                    body.extend(chunk)
                    if len(body) > MAX_ACCOUNT_BODY:
                        raise ValueError("account_response_oversized")
        raw = bytes(body)
        document = json.loads(
            raw,
            object_pairs_hook=unique_object,
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=invalid,
        )
        credits = document.get("credits") if type(document) is dict else None
        if (
            not isinstance(credits, Decimal)
            or not credits.is_finite()
            or not Decimal(0) <= credits <= Decimal("1000000000000")
        ):
            raise ValueError("invalid_credit_balance")
        exponent = credits.as_tuple().exponent
        if not isinstance(exponent, int) or not -12 <= exponent <= 12:
            raise ValueError("invalid_credit_balance_precision")
    except Exception:
        # No HTTP error, response body or credential enters shared diagnostics.
        raise AccountProbeUnavailable("BFL account response unavailable") from None
    return {
        "result": "BFL_CREDIT_BALANCE_OBSERVED",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": ACCOUNT_ENDPOINT,
        "credential_fingerprint": fingerprint,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "credit_balance": format(credits, "f"),
        "currency": "BFL_CREDIT",
        "positive_balance": credits > 0,
        "transport": "offline_mock" if isinstance(transport, httpx.MockTransport) else "https_get",
        "merchant_identity_verified": False,
        "models_verified": False,
        "spending_authorized": False,
        "paid_model_calls": 0,
    }, raw
