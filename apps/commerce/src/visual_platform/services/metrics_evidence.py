"""Cross-check runtime metric evidence; unknown money and missing samples stay unknown."""

import math
import re
from decimal import Decimal, InvalidOperation

PHASES = {
    "submitting",
    "polling",
    "archiving",
    "protecting",
    "detecting",
    "validating_rules",
    "finalizing",
}
PROVIDERS = {"fake", "bfl_mock", "gemini_mock", "bfl", "gemini", "other"}


def require(value, message):
    if not value:
        raise ValueError(message)


def count(value):
    return type(value) is int and value >= 0


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def validate_runtime_evidence(document, commit):
    require(
        document.get("result") == "PASS"
        and document.get("commit") == commit
        and type(document.get("paid_calls")) is int
        and document["paid_calls"] == 0,
        "runtime evidence failed or stale",
    )
    metrics = document["metrics"]
    telemetry = document["telemetry"]
    require(
        telemetry.get("coverage_complete_for_supplied_files") is True
        and telemetry.get("inputs_truncated") is False
        and type(telemetry.get("malformed_observations")) is int
        and telemetry["malformed_observations"] == 0,
        "telemetry coverage incomplete",
    )
    require(
        count(telemetry.get("api_samples"))
        and telemetry["api_samples"] > 0
        and telemetry.get("p95_method") == "nearest_rank",
        "API samples or quantile method missing",
    )
    require(
        finite(metrics.get("api_p95_ms")) and metrics["api_p95_ms"] == telemetry.get("api_p95_ms"),
        "API p95 missing or inconsistent",
    )
    require(
        count(metrics.get("api_5xx_count"))
        and metrics["api_5xx_count"] <= telemetry["api_samples"]
        and metrics["api_5xx_count"] == telemetry.get("api_5xx_count"),
        "5xx count missing or inconsistent",
    )
    failures = metrics.get("archive_failure_count")
    require(
        count(failures)
        and failures > 0
        and failures == telemetry.get("archive_failure_count")
        and count(telemetry.get("archive_operations"))
        and telemetry["archive_operations"] > failures
        and document.get("archive_fault_recovered") is True,
        "archive fault/recovery observations missing",
    )
    phases = metrics.get("task_stage_duration")
    require(isinstance(phases, list) and 0 < len(phases) <= len(PHASES), "phase metrics missing")
    names = []
    for phase in phases:
        name = phase.get("phase")
        require(name in PHASES and name not in names, "invalid or duplicate phase")
        names.append(name)
        require(
            count(phase.get("samples"))
            and phase["samples"] > 0
            and finite(phase.get("p95_seconds"))
            and finite(phase.get("total_seconds")),
            "phase observation missing",
        )
    activity = metrics.get("provider_submission_count", {}).get("activity")
    require(isinstance(activity, list) and 0 < len(activity) <= 12, "provider observations missing")
    names = []
    for row in activity:
        key = (row.get("provider"), row.get("step"))
        require(
            key[0] in PROVIDERS and key[1] in ("generate", "detect") and key not in names,
            "invalid provider labels",
        )
        names.append(key)
        require(
            all(
                count(row.get(key))
                for key in ("submission_intents", "receipt_count", "uncertain_count")
            )
            and row["receipt_count"] <= row["submission_intents"]
            and row["uncertain_count"] <= row["submission_intents"],
            "provider observations inconsistent",
        )
    require(
        sum(row["submission_intents"] for row in activity) > 0, "provider intent sample missing"
    )
    costs = metrics.get("provider_cost_by_currency")
    require(isinstance(costs, list) and 0 < len(costs) <= 1000, "cost observations missing")
    unknown = 0
    groups = []
    for row in costs:
        monetary_group = (row.get("provider"), row.get("currency"), row.get("cost_status"))
        require(
            monetary_group[0] in PROVIDERS
            and monetary_group[2] in ("estimated", "confirmed", "unknown")
            and monetary_group not in groups,
            "invalid or duplicate monetary group",
        )
        groups.append(monetary_group)
        require(
            monetary_group[1] is None
            or (
                isinstance(monetary_group[1], str)
                and re.fullmatch("[A-Za-z0-9_-]{1,16}", monetary_group[1])
            ),
            "invalid currency unit",
        )
        require(count(row.get("attempts")) and row["attempts"] > 0, "cost attempt count missing")
        if monetary_group[2] == "unknown":
            require(row.get("amount") is None, "unknown money cannot be zero")
            unknown += row["attempts"]
        else:
            require(
                isinstance(monetary_group[1], str)
                and re.fullmatch("[A-Za-z0-9_-]{1,16}", monetary_group[1])
                and isinstance(row.get("amount"), str),
                "monetary units or decimal text missing",
            )
            try:
                amount = Decimal(row["amount"])
            except InvalidOperation:
                raise ValueError("invalid monetary decimal") from None
            require(amount.is_finite() and amount >= 0, "nonfinite or negative money")
    require(
        count(metrics.get("provider_cost_unknown_records"))
        and metrics["provider_cost_unknown_records"] == unknown,
        "unknown cost count inconsistent",
    )
    rate = metrics.get("rule_manual_review_rate")
    require(finite(rate) and rate <= 1, "rule manual rate missing or invalid")
    return {
        "api_samples": telemetry["api_samples"],
        "archive_operations": telemetry["archive_operations"],
        "phase_groups": len(phases),
        "monetary_groups": len(costs),
    }
