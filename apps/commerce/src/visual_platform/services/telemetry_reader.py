"""Bounded read-only log aggregation; incomplete inputs never become zero errors."""

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

API = "api_request_observed"
ARCHIVE = "storage_archive_observed"


def parse_observation(value):
    if not isinstance(value, dict) or value.get("event") not in (API, ARCHIVE):
        raise ValueError("not a telemetry observation")
    expected = {"event", "observation_id", "observed_at", "duration_ms", "outcome"} | (
        {"status"} if value["event"] == API else set()
    )
    if value["event"] == API and "request_id" in value:
        expected.add("request_id")
        if (
            type(value["request_id"]) is not str
            or str(UUID(value["request_id"])) != value["request_id"]
        ):
            raise ValueError("noncanonical request identity")
    if set(value) != expected:
        raise ValueError("unexpected telemetry fields")
    if str(UUID(value["observation_id"])) != value["observation_id"]:
        raise ValueError("noncanonical observation identity")
    observed = datetime.fromisoformat(value["observed_at"])
    if observed.tzinfo is None:
        raise ValueError("timezone required")
    duration = value["duration_ms"]
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
        raise ValueError("invalid duration")
    outcomes = (
        ("completed", "exception", "interrupted", "incomplete")
        if value["event"] == API
        else ("success", "failure", "interrupted")
    )
    if value["outcome"] not in outcomes:
        raise ValueError("invalid observation outcome")
    if value["event"] == API and (
        type(value["status"]) is not int or not 100 <= value["status"] <= 599
    ):
        raise ValueError("invalid response status")
    return observed


def summarize_telemetry(
    api_logs=(), archive_logs=(), *, now=None, max_bytes=32 * 1024 * 1024, max_records=100000
):
    if (
        type(max_bytes) is not int
        or max_bytes < 1
        or type(max_records) is not int
        or max_records < 1
    ):
        raise ValueError("positive collection limits required")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("timezone required")
    sources: dict[Path, set[str]] = {}
    for paths, event in ((api_logs, API), (archive_logs, ARCHIVE)):
        for path in paths:
            sources.setdefault(Path(path).resolve(), set()).add(event)
    if len(sources) > 8:
        raise ValueError("at most eight explicit telemetry files")
    seen: dict[str, str] = {}
    durations = []
    errors = 0
    interrupted = 0
    archive_count = 0
    archive_failures = 0
    archive_interrupted = 0
    malformed = 0
    duplicates = 0
    truncated = False
    for path, events in sources.items():
        if not path.is_file():
            raise ValueError("telemetry log unavailable")
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                truncated = True
            data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            truncated = True
            data = data[:max_bytes]
        for raw in data.splitlines():
            # Own telemetry handler emits a complete JSON line. Do not interpret
            # JSON embedded in access-log paths or other untrusted text.
            if not raw.startswith(b'{"event":'):
                continue
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeError):
                if any(name.encode() in raw[:100] for name in events):
                    malformed += 1
                continue
            if not isinstance(value, dict) or value.get("event") not in events:
                continue
            try:
                observed = parse_observation(value)
            except (ValueError, TypeError, KeyError, AttributeError):
                malformed += 1
                continue
            if observed > now + timedelta(seconds=60):
                malformed += 1
                continue
            if observed < now - timedelta(hours=24):
                continue
            identity = value["observation_id"]
            fingerprint = hashlib.sha256(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if identity in seen:
                if seen[identity] != fingerprint:
                    malformed += 1
                else:
                    duplicates += 1
                continue
            if len(seen) >= max_records:
                truncated = True
                continue
            seen[identity] = fingerprint
            if value["event"] == API:
                if value["outcome"] in ("completed", "exception"):
                    durations.append(value["duration_ms"])
                    errors += int(value["status"] >= 500)
                else:
                    interrupted += 1
            else:
                if value["outcome"] == "interrupted":
                    archive_interrupted += 1
                else:
                    archive_count += 1
                    archive_failures += int(value["outcome"] == "failure")
    durations.sort()
    return {
        "scope": "24h observations in explicitly supplied files only; not proof of all instances or rotated log coverage",
        "api_duration_scope": "ASGI body completion or application exception; health probes and task SSE excluded",
        "archive_scope": "immutable object write plus read-back verification attempts, not unique assets or provider submissions",
        "observed_at": now.isoformat(),
        "files_read": len(sources),
        "observations": len(seen),
        "duplicate_observations": duplicates,
        "malformed_observations": malformed,
        "inputs_truncated": truncated,
        "coverage_complete_for_supplied_files": not truncated and malformed == 0,
        "api_samples": len(durations),
        "api_p95_ms": durations[math.ceil(len(durations) * 0.95) - 1] if durations else None,
        "p95_method": "nearest_rank",
        "api_5xx_count": errors if durations else None,
        "api_incomplete_or_interrupted": interrupted,
        "archive_operations": archive_count,
        "archive_failure_count": archive_failures if archive_count else None,
        "archive_interrupted": archive_interrupted,
    }
