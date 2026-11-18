"""Read-only, snapshot-consistent Qwen scope review. Never grants or executes spending."""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from visual_platform.db.result import required_row
from visual_platform.domain.provider_pricing import (
    SupplierPriceVersion,
    canonical_price,
    estimate_supplier_cost,
)
from visual_platform.providers.authorization import (
    BailianSmokeAuthorization,
    canonical_authorization,
)
from visual_platform.providers.bailian_connection import BailianConnection
from visual_platform.providers.runtime import ProviderRuntime
from visual_platform.services.rule_operations import assert_operator
from visual_platform.services.runtime_quote import validate_price_bindings
from visual_platform.storage.s3 import StorageError


def load_bound_review(dsn, user: UUID, quote: UUID, runtime_sha: str, authorization: UUID):
    """One read-only snapshot using scoped Ops functions and existing price-read permissions."""
    with psycopg.connect(dsn) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        assert_operator(connection)

        def value(query, arguments):
            return required_row(connection.execute(query, arguments))[0]

        reviewed = value("SELECT ops.provider_quote_review(%s,%s)", (user, quote))
        bindings = validate_price_bindings(
            reviewed["input_snapshot"]["_execution"].get("provider_price_versions")
        )
        return {
            "quote": reviewed,
            "runtime": value("SELECT ops.provider_runtime_context(%s)", (runtime_sha,)),
            "budget": value("SELECT ops.provider_budget_snapshot(%s)", (authorization,)),
            "prices": {
                step: value("SELECT ops.supplier_price_context(%s)", (UUID(binding["id"]),))
                for step, binding in bindings.items()
            },
            "price_evidence": {
                step: required_row(
                    connection.execute(
                        "SELECT evidence_bucket,evidence_object_key,evidence_storage_version_id,evidence_sha256 "
                        "FROM ops.supplier_price_versions WHERE id=%s AND document_sha256=%s",
                        (UUID(binding["id"]), binding["document_sha256"]),
                    )
                )
                for step, binding in bindings.items()
            },
            "projection": value("SELECT ops.qwen_generation_projection(%s,%s)", (user, quote)),
        }


def verify_price_evidence(storage, bundle):
    """Read exact private immutable evidence versions; never return their content or location."""
    verified = {}
    for step in ("generate", "detect"):
        verified[step] = False
        reference = bundle["price_evidence"].get(step)
        price = bundle["prices"].get(step)
        if reference is None or price is None or len(reference) != 4:
            continue
        bucket, key, version, digest = reference
        if (
            bucket != storage.bucket
            or type(key) is not str
            or type(digest) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not re.fullmatch(r"private/supplier-price/[0-9a-f-]{36}/" + digest, key)
            or digest != price["document"].get("evidence_sha256")
        ):
            continue
        try:
            data, _, actual_version = storage.get(key, version=version, limit=20 * 1024 * 1024)
            verified[step] = (
                bool(data)
                and hashlib.sha256(data).hexdigest() == digest
                and (version is None or actual_version == version)
            )
        except (BotoCoreError, ClientError, StorageError, OSError, ValueError):
            continue
    return verified


def review_bound_scope(
    scope,
    runtime,
    local_connection,
    bundle,
    *,
    now=None,
    credentials=None,
    allow_offline_fixture=False,
):
    """Validate a loaded snapshot; even a successful review is not execution authority."""
    scope = BailianSmokeAuthorization.model_validate_json(canonical_authorization(scope))
    runtime = ProviderRuntime.model_validate(runtime.model_dump(mode="json"), strict=True)
    local_connection = BailianConnection.model_validate(local_connection.model_dump(), strict=True)
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() != timedelta(0):
        raise ValueError("explicit_utc_review_time_required")
    if runtime.schema_version != 2 or type(allow_offline_fixture) is not bool:
        raise ValueError("explicit_bailian_review_required")
    quote = bundle["quote"]
    snapshot = quote["input_snapshot"]
    execution = snapshot["_execution"]
    checked = {}

    def check(name, condition):
        checked[name] = bool(condition)

    def future(value):
        parsed = datetime.fromisoformat(value)
        return parsed.tzinfo is not None and instant < parsed

    canonical = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    check(
        "quote_hash",
        hashlib.sha256(canonical.encode()).hexdigest()
        == quote["input_sha256"]
        == scope.quote_input_sha256,
    )
    check(
        "scope_identity",
        quote["user_id"] == str(scope.user_id)
        and snapshot["product_id"] == str(scope.product_id)
        and snapshot["source_asset_version_id"] == str(scope.source_asset_version_id)
        and execution["source_sha256"] == scope.source_sha256,
    )
    check(
        "business_scope",
        snapshot["generation"] == scope.generation.model_dump(mode="json")
        and snapshot["targets"] == [target.model_dump(mode="json") for target in scope.targets],
    )
    check(
        "scope_current",
        scope.authorized_at <= instant < scope.expires_at and future(quote["expires_at"]),
    )
    check(
        "runtime_binding",
        all(execution.get(key) == value for key, value in runtime.execution_binding().items()),
    )
    registered = bundle["runtime"]
    check(
        "registered_runtime_active",
        registered is not None
        and registered.get("revoked") is False
        and registered.get("document_sha256") == runtime.digest()
        and registered.get("document") == runtime.model_dump(mode="json"),
    )
    check(
        "local_connection_binding",
        local_connection.endpoint_origin == runtime.generate.endpoint_origin
        and local_connection.credential_fingerprint == runtime.generate.credential_fingerprint,
    )
    budget = bundle["budget"] or {}
    scope_sha = hashlib.sha256(canonical_authorization(scope)).hexdigest()
    check(
        "budget_scope",
        budget.get("authorization_id") == str(scope.authorization_id)
        and budget.get("quote_id") == quote["quote_id"]
        and budget.get("scope_sha256") == scope_sha
        and budget.get("schema_version") == 4
        and budget.get("profile") == "offline_protocol_mock",
    )
    check(
        "unused_open_budget",
        budget.get("state") == "open"
        and budget.get("holds") == []
        and budget.get("blocked_for_cost_review") is False
        and bool(budget.get("expires_at"))
        and future(budget["expires_at"]),
    )
    expected_steps = {
        step: (row.currency, Decimal(row.ceiling))
        for step, row in (
            ("generate", scope.step_budgets.generate),
            ("detect", scope.step_budgets.detect),
        )
    }
    steps = budget.get("step_limits", [])
    totals = budget.get("limits_by_currency", [])
    check(
        "budget_units",
        len(steps) == 2
        and {row["step"]: (row["currency"], Decimal(row["ceiling"])) for row in steps}
        == expected_steps
        and len(totals) == len(scope.currency_totals)
        and {row["currency"]: Decimal(row["total_ceiling"]) for row in totals}
        == {row.currency: Decimal(row.ceiling) for row in scope.currency_totals},
    )
    bindings = validate_price_bindings(execution.get("provider_price_versions"))
    prices = {}
    for step, binding in bindings.items():
        stored = bundle["prices"].get(step)
        if stored is None:
            check(step + "_price", False)
            continue
        price = SupplierPriceVersion.model_validate_json(json.dumps(stored["document"]))
        prices[step] = price
        selected = getattr(runtime, step)
        expected_provider = "aliyun_bailian" if step == "generate" else "gemini"
        required_meters = (
            {"qima_input_1k", "qima_input_2k", "qima_output_1k", "qima_output_2k"}
            if step == "generate"
            else {"input_tokens", "cached_input_tokens", "output_tokens", "thinking_tokens"}
        )
        check(
            step + "_price",
            stored["id"] == binding["id"]
            and stored["document_sha256"]
            == binding["document_sha256"]
            == hashlib.sha256(canonical_price(price)).hexdigest()
            and (
                price.provider,
                price.model,
                price.endpoint_origin,
                price.merchant_fingerprint,
                price.billing_group,
            )
            == (
                expected_provider,
                selected.model,
                selected.endpoint_origin,
                selected.merchant_fingerprint,
                selected.billing_group,
            )
            and price.currency == expected_steps[step][0]
            and {rate.measure for rate in price.rates} == required_meters
            and price.valid_from <= instant < price.valid_until
            and stored.get("evidence_archived") is True,
        )
        check(
            step + "_price_evidence_kind",
            price.evidence_kind == "reviewed_price"
            or (allow_offline_fixture and price.evidence_kind == "offline_fixture"),
        )
    projection = bundle["projection"] or {}
    generation_price = prices.get("generate")
    if generation_price is not None:
        width = scope.generation.output.width
        height = scope.generation.output.height
        tier = "1k" if width * height <= 2_250_000 else "2k"
        quantities = {
            "qima_input_1k": int(tier == "1k"),
            "qima_input_2k": int(tier == "2k"),
            "qima_output_1k": int(tier == "1k"),
            "qima_output_2k": int(tier == "2k"),
        }
        estimate = estimate_supplier_cost(
            generation_price,
            quantities,
            provider="aliyun_bailian",
            model=runtime.generate.model,
            endpoint_origin=runtime.generate.endpoint_origin,
            merchant_fingerprint=runtime.generate.merchant_fingerprint,
            billing_group=runtime.generate.billing_group,
            observed_at=instant,
        )
        check(
            "request_prediction",
            projection.get("projection_status") == "projected"
            and projection.get("quote_input_sha256") == quote["input_sha256"]
            and projection.get("price_document_sha256") == bindings["generate"]["document_sha256"]
            and projection.get("quantities") == quantities
            and projection.get("amount") == estimate["amount"]
            and estimate["amount"] is not None
            and projection.get("currency") == "CNY"
            and projection.get("supplier_usage_observed") is False
            and Decimal(estimate["amount"]) <= Decimal(scope.step_budgets.generate.ceiling),
        )
    else:
        check("request_prediction", False)
    if credentials is not None:
        check(
            "credential_matches",
            type(credentials) is dict
            and set(credentials) == {"generate", "detect"}
            and all(value is True for value in credentials.values()),
        )
    passed = all(checked.values())
    return {
        "result": "BOUND_PROTOCOL_SCOPE_REVIEW" if passed else "BOUND_SCOPE_REJECTED",
        "scope_checks_passed": passed,
        "review_mode": "offline_fixture" if allow_offline_fixture else "reviewed_price",
        "checks": checked,
        "failures": [name for name, value in checked.items() if not value],
        "credential_check_performed": credentials is not None,
        "runtime_sha256": runtime.digest(),
        "quote_input_sha256": quote["input_sha256"],
        "scope_sha256": scope_sha,
        "paid_calls": 0,
        "execution_enabled": False,
        "actual_spending_verified": False,
        "not_verified": [
            "remote authentication and compatibility",
            "current S3 price evidence availability",
            "live execution permission and routing",
            "real image quality",
            "actual supplier invoice",
        ],
        "advisory_snapshot_only": True,
    }
