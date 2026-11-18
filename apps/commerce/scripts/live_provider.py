"""Explicitly enable/disable reviewed live routing for one user; never calls a model."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import psycopg
from psycopg.types.json import Jsonb

from visual_platform.providers.runtime import credential_status, load_runtime
from visual_platform.services.paid_bailian_smoke import reference_prices
from visual_platform.services.rule_operations import assert_operator
from visual_platform.services.runtime_quote import register_runtime_configuration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--enable", action="store_true")
    mode.add_argument("--disable", action="store_true")
    parser.add_argument("--user", type=UUID)
    parser.add_argument("--operation-id", type=UUID)
    parser.add_argument("--reason")
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--generation-catalogue", type=Path)
    parser.add_argument("--detection-catalogue", type=Path)
    args = parser.parse_args()
    if not args.enable and not args.disable:
        print(json.dumps({"result": "LIVE_CONFIGURATION_NOT_REQUESTED", "paid_calls": 0}))
        return 3
    if None in (args.user, args.operation_id, args.reason):
        parser.error("explicit user, operation-id and reason required")
    dsn = os.environ["ADMIN_DATABASE_URL"]
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
    if args.disable:
        with psycopg.connect(dsn) as connection:
            connection.execute(
                "SELECT ops.deactivate_live_provider(%s,%s,%s)",
                (args.user, args.operation_id, args.reason),
            )
    else:
        if None in (args.runtime, args.generation_catalogue, args.detection_catalogue):
            parser.error("enable requires reviewed runtime and both original catalogue files")
        runtime = load_runtime(args.runtime)
        if runtime.schema_version != 3 or not all(credential_status(runtime).values()):
            raise ValueError("matching_private_dual_bailian_credentials_required")
        rates, evidence = {}, {}
        for step, model, path in (
            ("generate", runtime.generate.model, args.generation_catalogue),
            ("detect", runtime.detect.model, args.detection_catalogue),
        ):
            if (
                path.is_symlink()
                or path.is_junction()
                or not path.is_file()
                or path.stat().st_size > 1024 * 1024
            ):
                raise ValueError("bounded_original_catalogue_required")
            raw = path.read_bytes()
            if len(raw) > 1024 * 1024:
                raise ValueError("bounded_original_catalogue_required")
            rates[step] = reference_prices(raw, model)
            evidence[step] = hashlib.sha256(raw).hexdigest()
        register_runtime_configuration(
            dsn, runtime, runtime.digest(), uuid5(args.operation_id, "runtime"), args.reason
        )
        with psycopg.connect(dsn) as connection:
            connection.execute(
                "SELECT ops.activate_live_provider(%s,%s,%s,%s,true,%s,%s)",
                (
                    args.user,
                    runtime.digest(),
                    Jsonb(rates),
                    Jsonb(evidence),
                    args.operation_id,
                    args.reason,
                ),
            )
    print(
        json.dumps(
            {
                "result": "LIVE_USER_ENABLED" if args.enable else "LIVE_USER_DISABLED",
                "user_id": str(args.user),
                "spending_limit_cny": None,
                "paid_calls": 0,
                "runtime_processes_changed": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(
            json.dumps(
                {
                    "result": "LIVE_CONFIGURATION_REJECTED",
                    "error_type": type(error).__name__,
                    "paid_calls": 0,
                }
            )
        )
        raise SystemExit(1)
