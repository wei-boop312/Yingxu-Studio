"""Review explicit provider bindings locally; never activate a live Worker or call models."""

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.providers.runtime import credential_status, load_runtime
from visual_platform.services.runtime_quote import (
    load_price_bindings,
    prepare_runtime_quote,
    register_runtime_configuration,
    registered_runtime_context,
    revoke_runtime_configuration,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-credentials", action="store_true")
    mode.add_argument("--prepare-protocol-quote", action="store_true")
    mode.add_argument("--register-config", action="store_true")
    mode.add_argument("--revoke-config", action="store_true")
    for name in ("user", "source-quote", "operation-id"):
        parser.add_argument("--" + name, type=UUID)
    parser.add_argument("--reviewed-sha256")
    parser.add_argument("--reason")
    parser.add_argument("--price-bindings", type=Path)
    args = parser.parse_args()
    try:
        runtime = load_runtime(args.config)
        if args.price_bindings is not None and not args.prepare_protocol_quote:
            raise ValueError("price_bindings_only_for_explicit_quote_preparation")
        write_args = (
            args.user,
            args.source_quote,
            args.operation_id,
            args.reviewed_sha256,
            args.reason,
        )
        if args.register_config or args.revoke_config:
            if (
                args.user is not None
                or args.source_quote is not None
                or any(
                    value is None
                    for value in (args.operation_id, args.reviewed_sha256, args.reason)
                )
            ):
                raise ValueError("explicit_runtime_registration_scope_required")
            if args.reviewed_sha256 != runtime.digest():
                raise ValueError("runtime_configuration_not_reviewed")
            dsn = os.environ.get("ADMIN_DATABASE_URL")
            if not dsn:
                print(json.dumps({"result": "BLOCKED_ENV", "paid_calls": 0}))
                return 3
            identity = (
                revoke_runtime_configuration(dsn, runtime.digest(), args.operation_id, args.reason)
                if args.revoke_config
                else register_runtime_configuration(
                    dsn, runtime, args.reviewed_sha256, args.operation_id, args.reason
                )
            )
            context = registered_runtime_context(dsn, runtime.digest())
            print(
                json.dumps(
                    {
                        "result": "RUNTIME_CONFIG_REVOKED"
                        if args.revoke_config
                        else "OFFLINE_RUNTIME_CONFIG_REGISTERED",
                        "runtime_id": identity,
                        "configuration_sha256": runtime.digest(),
                        "execution_enabled": False,
                        "merchant_identity_verified": False,
                        "runtime_revoked": context["revoked"],
                        "previously_committed_intents_may_have_outcomes": True,
                        "paid_calls": 0,
                    }
                )
            )
            return 0
        if args.prepare_protocol_quote:
            if any(value is None for value in write_args):
                raise ValueError("explicit_runtime_quote_scope_required")
            if runtime.schema_version == 3:
                print(
                    json.dumps(
                        {
                            "result": "RUNTIME_QUOTE_NOT_IMPLEMENTED",
                            "reason": "qwen_vision_quote_budget_path_not_enabled",
                            "execution_enabled": False,
                            "paid_calls": 0,
                        }
                    )
                )
                return 3
            price_versions = (
                load_price_bindings(args.price_bindings)
                if args.price_bindings is not None
                else None
            )
            dsn = os.environ.get("ADMIN_DATABASE_URL")
            if not dsn:
                print(json.dumps({"result": "BLOCKED_ENV", "paid_calls": 0}))
                return 3
            result = prepare_runtime_quote(
                dsn,
                args.user,
                args.source_quote,
                runtime,
                args.reviewed_sha256,
                args.operation_id,
                args.reason,
                price_versions=price_versions,
            )
            print(json.dumps({"result": "OFFLINE_RUNTIME_QUOTE_PREPARED", **result}))
            return 0
        if any(value is not None for value in write_args):
            raise ValueError("unexpected_runtime_quote_scope")
        keys = credential_status(runtime) if args.check_credentials else None
        ready = keys is None or all(keys.values())
        print(
            json.dumps(
                {
                    "result": "RUNTIME_BINDING_REVIEW_READY"
                    if ready
                    else "WAITING_PRIVATE_CREDENTIALS",
                    "configuration_sha256": runtime.digest(),
                    "models": runtime.execution_binding()["provider_models"],
                    "endpoints": runtime.execution_binding()["provider_endpoints"],
                    "key_environment": {
                        "generate": runtime.generate.key_environment,
                        "detect": runtime.detect.key_environment,
                    },
                    "reviewed_credential_present": keys,
                    "credential_identity_verified": False,
                    "merchant_identity_verified": False,
                    "supplier_compatibility_verified": False,
                    "protocol_quote_preparation_supported": runtime.schema_version != 3,
                    "additional_implementation_required": (
                        ["dual_model_quote_budget_admission", "qwen_vision_pg_detection_archive"]
                        if runtime.schema_version == 3
                        else []
                    ),
                    "execution_enabled": False,
                    "paid_calls": 0,
                }
            )
        )
        return 0 if ready else 3
    except Exception as error:
        print(
            json.dumps(
                {
                    "result": "RUNTIME_BINDING_REJECTED",
                    "error_type": type(error).__name__,
                    "paid_calls": 0,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
