"""Strict OpenAI-compatible Qwen counters, distinct from Gemini metering."""


def vision_usage(document):
    raw = document.get("usage")
    result = {"usage": None, "quantities": None, "billing_usage_integrity": "missing"}
    if raw is None:
        return result
    if type(raw) is not dict:
        return {**result, "billing_usage_integrity": "invalid"}
    names = {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_tokens_details",
        "completion_tokens_details",
    }
    counts = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if name in raw:
            value = raw[name]
            if type(value) is not int or not 0 <= value <= 1_000_000_000_000:
                return {**result, "billing_usage_integrity": "invalid"}
            counts[name] = value
    if set(raw) != names:
        return {**result, "usage": counts, "billing_usage_integrity": "incomplete_or_extra"}
    for parent, field in (
        ("prompt_tokens_details", "cached_tokens"),
        ("completion_tokens_details", "reasoning_tokens"),
    ):
        nested = raw[parent]
        if type(nested) is not dict or set(nested) != {field}:
            return {**result, "usage": counts, "billing_usage_integrity": "incomplete_or_extra"}
        value = nested[field]
        if type(value) is not int or not 0 <= value <= 1_000_000_000_000:
            return {**result, "usage": counts, "billing_usage_integrity": "invalid"}
        counts[field] = value
    prompt, completion, total = (
        counts[name] for name in ("prompt_tokens", "completion_tokens", "total_tokens")
    )
    cached, reasoning = counts["cached_tokens"], counts["reasoning_tokens"]
    # The frozen detector request explicitly disables thinking.
    if cached > prompt or reasoning != 0 or total != prompt + completion:
        return {**result, "usage": counts, "billing_usage_integrity": "contradictory"}
    return {
        "usage": counts,
        "billing_usage_integrity": "complete",
        "quantities": {
            "input_tokens": prompt - cached,
            "cached_input_tokens": cached,
            "output_tokens": completion,
        },
    }
