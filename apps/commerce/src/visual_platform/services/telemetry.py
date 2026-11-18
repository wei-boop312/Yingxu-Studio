"""Sanitized application observations; no request, object or credential values."""

import json
import logging
import time
from datetime import datetime, timezone
from functools import wraps
from uuid import UUID, uuid4

LOGGER = logging.getLogger("visual_platform.telemetry")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


def emit_observation(event, **fields):
    record = {
        "event": event,
        "observation_id": str(uuid4()),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    try:
        LOGGER.info(json.dumps(record, separators=(",", ":"), allow_nan=False))
    except Exception:
        pass  # Instrumentation never changes delivery or credit state.


class RequestTelemetry:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or not path.startswith("/api/v1/")
            or path.startswith("/api/v1/health/")
            or (path.startswith("/api/v1/tasks/") and path.endswith("/events"))
        ):
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        status = 500
        outcome = "incomplete"
        completed_at = None
        send_failed = False

        async def observed_send(message):
            nonlocal status, outcome, completed_at, send_failed
            try:
                await send(message)
            except BaseException:
                # Include cancellation; record interruption and propagate it unchanged.
                send_failed = True
                raise
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                outcome = "completed"
                completed_at = time.perf_counter()

        try:
            return await self.app(scope, receive, observed_send)
        except Exception:
            if completed_at is None:
                outcome = "interrupted" if send_failed else "exception"
            raise
        except BaseException:
            # CancelledError must still escape; telemetry only labels the unfinished response.
            if completed_at is None:
                outcome = "interrupted"
            raise
        finally:
            correlation = {}
            state = scope.get("state", {})
            identity = state.get("request_id") if isinstance(state, dict) else None
            if type(identity) is str and len(identity) == 36:
                try:
                    if str(UUID(identity)) == identity:
                        correlation["request_id"] = identity
                except ValueError:
                    pass
            emit_observation(
                "api_request_observed",
                duration_ms=round(((completed_at or time.perf_counter()) - started) * 1000, 3),
                status=status,
                outcome=outcome,
                **correlation,
            )


def archive_observed(function):
    @wraps(function)
    def observed(*args, **kwargs):
        started = time.perf_counter()
        outcome = "interrupted"
        try:
            result = function(*args, **kwargs)
            outcome = "success"
            return result
        except Exception:
            outcome = "failure"
            raise
        finally:
            emit_observation(
                "storage_archive_observed",
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                outcome=outcome,
            )

    return observed
