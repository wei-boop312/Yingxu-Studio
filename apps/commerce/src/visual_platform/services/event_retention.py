"""Restricted tenant-scoped task event retention; never mutates business state."""

import psycopg

from visual_platform.db.result import required_row
from visual_platform.services.rule_operations import assert_operator


def event_retention(dsn, user, *, days=30, limit=1000, operation=None, reason=None):
    if (
        type(days) is not int
        or not 1 <= days <= 3650
        or type(limit) is not int
        or not 1 <= limit <= 10000
    ):
        raise ValueError("invalid_event_retention_scope")
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        if operation is None:
            if reason is not None:
                raise ValueError("event_retention_operation_required")
            result = required_row(
                connection.execute(
                    "SELECT ops.preview_task_event_retention(%s,%s,%s)", (user, days, limit)
                )
            )[0]
            return {"result": "TASK_EVENT_RETENTION_PLAN", "paid_calls": 0, **result}
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 4000:
            raise ValueError("event_retention_reason_required")
        result = required_row(
            connection.execute(
                "SELECT ops.prune_task_events(%s,%s,%s,%s,%s)",
                (user, days, limit, operation, reason),
            )
        )[0]
        return {"result": "TASK_EVENTS_PRUNED", "paid_calls": 0, **result}
