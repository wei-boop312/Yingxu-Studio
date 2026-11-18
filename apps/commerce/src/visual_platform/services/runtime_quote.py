"""Prepare a new audited protocol rehearsal quote; never mutate the original quote."""

import copy
import hashlib
import json
import re
from pathlib import Path
from uuid import UUID, uuid5

import psycopg

from visual_platform.db.result import required_row
from visual_platform.providers.gemini_usage import unique_object
from visual_platform.providers.qwen_image import checked_dimensions
from visual_platform.providers.runtime import ProviderRuntime
from visual_platform.services.rule_operations import assert_operator

NAMESPACE = UUID("b2bf2e62-cfa1-4d41-a267-96bcb09ae73c")


def register_runtime_configuration(dsn, runtime, reviewed_sha256, operation, reason):
    runtime = ProviderRuntime.model_validate(runtime.model_dump(mode="json"), strict=True)
    if runtime.digest() != reviewed_sha256:
        raise ValueError("runtime_configuration_not_reviewed")
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        identity = required_row(
            connection.execute(
                "SELECT ops.register_provider_runtime(%s,%s,%s,%s)",
                (runtime.canonical().decode(), runtime.digest(), operation, reason),
            )
        )[0]
    return str(identity)


def registered_runtime_context(dsn, digest):
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        context = required_row(
            connection.execute("SELECT ops.provider_runtime_context(%s)", (digest,))
        )[0]
    if context is None:
        raise ValueError("runtime_configuration_not_registered")
    runtime = ProviderRuntime.model_validate(context["document"], strict=True)
    if runtime.digest() != digest or context["document_sha256"] != digest:
        raise ValueError("stored_runtime_configuration_mismatch")
    return context


def revoke_runtime_configuration(dsn, digest, operation, reason):
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        identity = required_row(
            connection.execute(
                "SELECT ops.revoke_provider_runtime(%s,%s,%s)", (digest, operation, reason)
            )
        )[0]
    return str(identity)


def validate_price_bindings(price_versions):
    if type(price_versions) is not dict or set(price_versions) != {"generate", "detect"}:
        raise ValueError("invalid_runtime_price_bindings")
    for binding in price_versions.values():
        if type(binding) is not dict or set(binding) != {"id", "document_sha256"}:
            raise ValueError("invalid_runtime_price_bindings")
        if (
            type(binding["id"]) is not str
            or str(UUID(binding["id"])) != binding["id"]
            or type(binding["document_sha256"]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", binding["document_sha256"])
        ):
            raise ValueError("invalid_runtime_price_bindings")
    return copy.deepcopy(price_versions)


def load_price_bindings(path: Path):
    def invalid(value):
        raise ValueError("nonstandard_price_binding_json")

    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise ValueError("invalid_price_binding_file")
    with path.open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("invalid_price_binding_file")
    return validate_price_bindings(
        json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid)
    )


def runtime_snapshot(source, runtime, price_versions=None):
    if runtime.schema_version == 3:
        raise ValueError("qwen_vision_quote_budget_path_not_enabled")
    document = copy.deepcopy(source["input_snapshot"])
    if runtime.schema_version == 2:
        output = document["generation"]["output"]
        checked_dimensions(output["width"], output["height"])
    document["_execution"].update(runtime.execution_binding())
    document["_execution"].update(
        provider_profile="offline_protocol_mock",
        provider_model=runtime.generate.model + "-protocol-mock",
        price_version="trial-protocol-mock-v1",
    )
    if price_versions is not None:
        if runtime.schema_version != 2:
            raise ValueError("invalid_runtime_price_bindings")
        document["_execution"]["provider_price_versions"] = validate_price_bindings(price_versions)
    return document


def prepare_runtime_quote(
    dsn, user, source_quote, runtime, reviewed_sha256, operation, reason, *, price_versions=None
):
    runtime = ProviderRuntime.model_validate(runtime.model_dump(mode="json"), strict=True)
    if reviewed_sha256 != runtime.digest():
        raise ValueError("runtime_configuration_not_reviewed")
    if runtime.schema_version == 3:
        raise ValueError("qwen_vision_quote_budget_path_not_enabled")
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        source = required_row(
            connection.execute("SELECT ops.runtime_quote_source(%s,%s)", (user, source_quote))
        )[0]
    snapshot = runtime_snapshot(source, runtime, price_versions)
    canonical = json.dumps(
        snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    identity = uuid5(NAMESPACE, str(operation))
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        identity = required_row(
            connection.execute(
                "SELECT ops.prepare_runtime_quote(%s,%s,%s,%s,%s,%s,%s)",
                (user, source_quote, identity, canonical, digest, operation, reason),
            )
        )[0]
    return {
        "quote_id": str(identity),
        "input_sha256": digest,
        "runtime_sha256": runtime.digest(),
        "profile": "offline_protocol_mock",
        "paid_calls": 0,
        "execution_enabled": False,
    }
