"""Nonempty optional detector identity/price recovery; never creates execution authority."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg

from visual_platform.domain.provider_pricing import SupplierPriceVersion, canonical_price
from visual_platform.providers.runtime import ProviderRuntime, credential_fingerprint
from visual_platform.services.provider_pricing import publish_supplier_price, supplier_price_context
from visual_platform.services.runtime_quote import (
    register_runtime_configuration,
    registered_runtime_context,
    runtime_snapshot,
)
from visual_platform.storage.s3 import S3Storage


def prepare_dual_bailian_restore(profile, settings):
    account = {
        "endpoint_origin": "https://ws-synthetic.cn-beijing.maas.aliyuncs.com",
        "merchant_fingerprint": "b" * 64,
        "credential_fingerprint": credential_fingerprint("synthetic-restore-no-real-key"),
        "billing_group": "offline-dual-restore",
    }
    runtime = ProviderRuntime.model_validate(
        {
            "schema_version": 3,
            "version": "restore-dual-" + uuid4().hex,
            "generate": {**account, "provider": "aliyun_bailian", "model": "qwen-image-3.0"},
            "detect": {**account, "provider": "aliyun_bailian_vision", "model": "qwen3.8-flash"},
        },
        strict=True,
    )
    operation = uuid4()
    reason = "synthetic dual-model recovery metadata; not spending permission"
    runtime_id = register_runtime_configuration(
        profile["ADMIN_DATABASE_URL"], runtime, runtime.digest(), operation, reason
    )
    now = datetime.now(timezone.utc)
    evidence = b"Synthetic optional detector price recovery fixture; not merchant rates."
    price = SupplierPriceVersion.model_validate_json(
        json.dumps(
            {
                "schema_version": 3,
                "provider": "aliyun_bailian_vision",
                "model": "qwen3.8-flash",
                "endpoint_origin": account["endpoint_origin"],
                "merchant_fingerprint": account["merchant_fingerprint"],
                "billing_group": account["billing_group"],
                "version": "restore-visual-" + uuid4().hex,
                "currency": "CNY",
                "valid_from": (now - timedelta(minutes=1)).isoformat(),
                "valid_until": (now + timedelta(hours=1)).isoformat(),
                "evidence_kind": "offline_fixture",
                "evidence_sha256": hashlib.sha256(evidence).hexdigest(),
                "complete_meter_coverage": True,
                "rates": [
                    {"measure": name, "amount": amount, "per": 1000000}
                    for name, amount in (
                        ("input_tokens", "0.12345678"),
                        ("cached_input_tokens", "0.23456789"),
                        ("output_tokens", "0.34567891"),
                    )
                ],
            }
        )
    )
    storage = S3Storage(settings)
    try:
        identity = publish_supplier_price(
            profile["ADMIN_DATABASE_URL"],
            storage,
            price,
            evidence,
            uuid4(),
            "synthetic optional detector price recovery",
            evidence_reviewed=True,
        )
    finally:
        storage.close()
    return {
        "runtime_id": runtime_id,
        "runtime_sha256": runtime.digest(),
        "registration_operation": str(operation),
        "registration_reason": reason,
        "price_id": identity,
        "price_sha256": hashlib.sha256(canonical_price(price)).hexdigest(),
        "evidence_sha256": hashlib.sha256(evidence).hexdigest(),
    }


def verify_dual_bailian_restore(profile, settings, state):
    context = registered_runtime_context(profile["ADMIN_DATABASE_URL"], state["runtime_sha256"])
    runtime = ProviderRuntime.model_validate(context["document"], strict=True)
    if context["id"] != state["runtime_id"] or context["execution_enabled"] is not False:
        raise RuntimeError("dual runtime restore identity or execution state mismatch")
    replay = register_runtime_configuration(
        profile["ADMIN_DATABASE_URL"],
        runtime,
        runtime.digest(),
        UUID(state["registration_operation"]),
        state["registration_reason"],
    )
    if replay != state["runtime_id"]:
        raise RuntimeError("dual runtime restore registration replay changed")
    try:
        runtime_snapshot({}, runtime)
    except ValueError as error:
        if str(error) != "qwen_vision_quote_budget_path_not_enabled":
            raise
    else:
        raise RuntimeError("restored dual metadata unexpectedly opened quote admission")
    price_context = supplier_price_context(profile["ADMIN_DATABASE_URL"], UUID(state["price_id"]))
    price = SupplierPriceVersion.model_validate_json(json.dumps(price_context["document"]))
    if (
        price_context["document_sha256"] != state["price_sha256"]
        or hashlib.sha256(canonical_price(price)).hexdigest() != state["price_sha256"]
        or price.provider != "aliyun_bailian_vision"
        or price_context["actual_spending_verified"] is not False
    ):
        raise RuntimeError("dual visual price restore integrity mismatch")
    with psycopg.connect(profile["ADMIN_DATABASE_URL"]) as connection:
        pointer = connection.execute(
            "SELECT evidence_object_key,evidence_storage_version_id FROM ops.supplier_price_versions WHERE id=%s",
            (UUID(state["price_id"]),),
        ).fetchone()
    if pointer is None:
        raise RuntimeError("restored visual price evidence pointer missing")
    storage = S3Storage(settings)
    try:
        evidence, _, _ = storage.get(pointer[0], version=pointer[1])
        if hashlib.sha256(evidence).hexdigest() != state["evidence_sha256"]:
            raise RuntimeError("restored visual price S3 evidence mismatch")
    finally:
        storage.close()
