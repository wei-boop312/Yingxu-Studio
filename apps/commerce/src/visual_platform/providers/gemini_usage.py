"""Validate wire usage before SDK coercion; never infer omitted billing meters."""

import json

COUNTS = {
    "promptTokenCount": "prompt_token_count",
    "candidatesTokenCount": "candidates_token_count",
    "cachedContentTokenCount": "cached_content_token_count",
    "thoughtsTokenCount": "thoughts_token_count",
    "toolUsePromptTokenCount": "tool_use_prompt_token_count",
    "totalTokenCount": "total_token_count",
}
DETAILS = {
    "promptTokensDetails",
    "cacheTokensDetails",
    "candidatesTokensDetails",
    "toolUsePromptTokensDetails",
    "serviceTier",
    "trafficType",
}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate wire response key")
        result[key] = value
    return result


def parse_wire_usage(body: bytes):
    try:
        payload = json.loads(body, object_pairs_hook=unique_object)
        raw = payload.get("usageMetadata")
    except (ValueError, TypeError, AttributeError):
        return {"usage": None, "billing_usage_integrity": "invalid"}
    if type(raw) is not dict:
        return {"usage": None, "billing_usage_integrity": "missing"}
    usage = {}
    invalid = False
    for wire, name in COUNTS.items():
        if wire in raw:
            value = raw[wire]
            if type(value) is not int or not 0 <= value <= 1_000_000_000_000:
                invalid = True
            else:
                usage[name] = value
    integrity = "complete" if set(usage) == set(COUNTS.values()) else "incomplete"
    if invalid:
        integrity = "invalid"
    if set(raw) - set(COUNTS) - DETAILS or raw.get("serviceTier", "standard") not in (
        "standard",
        "unspecified",
        "SERVICE_TIER_UNSPECIFIED",
    ):
        integrity = "unsupported"
    if integrity == "complete":
        if (
            usage["cached_content_token_count"] > usage["prompt_token_count"]
            or usage["total_token_count"]
            != usage["prompt_token_count"]
            + usage["candidates_token_count"]
            + usage["thoughts_token_count"]
            + usage["tool_use_prompt_token_count"]
        ):
            integrity = "invalid"
        elif usage["tool_use_prompt_token_count"]:
            integrity = "unsupported"
    return {"usage": usage or None, "billing_usage_integrity": integrity}
