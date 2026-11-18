"""Read restricted aggregate metrics; never retry suppliers or move credits."""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import aio_pika
import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from visual_platform.db.result import required_row
from visual_platform.services.monitoring import Thresholds, alerts, prometheus
from visual_platform.services.telemetry_reader import summarize_telemetry
from visual_platform.storage.s3 import S3Storage

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--prometheus", action="store_true")
p.add_argument("--probe-dependencies", action="store_true")
p.add_argument("--unknown-seconds", type=float, default=300)
p.add_argument("--outbox-seconds", type=float, default=30)
p.add_argument("--heartbeat-seconds", type=float, default=15)
p.add_argument("--api-log", type=Path, action="append", default=[])
p.add_argument("--archive-log", type=Path, action="append", default=[])
args = p.parse_args()
logging.getLogger("aiormq").setLevel(logging.CRITICAL)
logging.getLogger("aio_pika").setLevel(logging.CRITICAL)


async def dependencies():
    async def mq():
        connection = None
        try:
            url = os.environ["AMQP_URL"]
            u = urlsplit(url)
            if u.scheme not in ("amqp", "amqps") or (
                u.scheme == "amqp" and u.hostname not in ("127.0.0.1", "localhost")
            ):
                return "unavailable"
            async with asyncio.timeout(2):
                connection = await aio_pika.connect(url, timeout=1.5)
                channel = await connection.channel()
                await channel.declare_queue(
                    os.environ.get("QUEUE_PREFIX", "visual.v1") + ".generate",
                    passive=True,
                    timeout=1,
                )
            return "ready"
        except Exception:
            return "unavailable"
        finally:
            if connection:
                try:
                    await asyncio.wait_for(connection.close(), 1)
                except Exception:
                    pass

    async def storage():
        client = None
        try:
            values = {
                name.lower(): os.environ[name]
                for name in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET")
            }
            u = urlsplit(values["s3_endpoint"])
            if u.scheme not in ("http", "https") or (
                u.scheme == "http" and u.hostname not in ("127.0.0.1", "localhost")
            ):
                return "unavailable"
            client = S3Storage(
                SimpleNamespace(**values, s3_region=os.environ.get("S3_REGION", "us-east-1"))
            )
            await asyncio.to_thread(client.probe)
            return "ready"
        except Exception:
            return "unavailable"
        finally:
            if client:
                client.close()

    a, b = await asyncio.gather(mq(), storage())
    return {"mq": a, "storage": b}


try:
    thresholds = Thresholds(args.unknown_seconds, args.outbox_seconds, args.heartbeat_seconds)
    dsn = os.environ.get("MONITOR_DATABASE_URL") or os.environ.get("ADMIN_DATABASE_URL")
    if not dsn:
        p.error("dedicated MONITOR_DATABASE_URL or non-owner ADMIN_DATABASE_URL required")
    with psycopg.connect(
        dsn,
        connect_timeout=3,
        options="-c statement_timeout=3000 -c idle_in_transaction_session_timeout=5000",
    ) as c:
        c.execute("SET TRANSACTION READ ONLY")
        row = c.execute(
            "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,pg_has_role(current_user,'ops_monitor','MEMBER') OR pg_has_role(current_user,'ops_admin','MEMBER'),EXISTS(SELECT 1 FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname IN ('app','auth','ops') AND t.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)) FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        if not row or any(row[:4]) or not row[4] or row[5]:
            raise ValueError("monitor runtime role must be unprivileged and non-owner")
        snapshot = required_row(
            c.execute(
                "SELECT ops.monitor_snapshot() || jsonb_build_object('business_metrics',ops.business_metrics_snapshot())"
            )
        )[0]
    deps = asyncio.run(dependencies()) if args.probe_dependencies else None
    if args.api_log or args.archive_log:
        telemetry = summarize_telemetry(args.api_log, args.archive_log)
        snapshot["request_archive_metrics"] = telemetry
        if not telemetry["coverage_complete_for_supplied_files"]:
            raise ValueError("incomplete telemetry inputs")
        missing = []
        if not telemetry["api_samples"]:
            missing.append("api_duration_and_5xx")
        if not telemetry["archive_operations"]:
            missing.append("archive_failure_count")
        snapshot["measurement_coverage"] = {"missing": missing, "scope": telemetry["scope"]}
    findings = alerts(snapshot, thresholds, deps)
    if args.prometheus:
        print(prometheus(snapshot, thresholds, deps), end="")
    else:
        print(
            json.dumps(
                {"snapshot": snapshot, "dependencies": deps, "alerts": findings}, ensure_ascii=False
            )
        )
    raise SystemExit(
        2 if any(x["severity"] == "critical" for x in findings) else 1 if findings else 0
    )
except Exception:
    print(
        "visual_monitor_scrape_error 1"
        if args.prometheus
        else json.dumps(
            {
                "error": "monitor_unavailable",
                "details": "check private role and dependency configuration",
            }
        )
    )
    raise SystemExit(3)
