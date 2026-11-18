from pydantic import ValidationError

from visual_platform.domain.rules import RuleConfig


def evaluate_rules(final_sha256, rules, facts, local, *, sampling_selected=False, hard_failures=()):
    if len(final_sha256) != 64 or any(c not in "0123456789abcdef" for c in final_sha256):
        raise ValueError("invalid final hash")
    results = []
    for rule in rules:
        invalid_config = False
        try:
            config = RuleConfig.model_validate(rule.get("config", {}))
        except ValidationError:
            config = RuleConfig()
            invalid_config = True
        verified = rule.get("evidence_status") == "verified" and not invalid_config
        reasons = []
        checks = []
        failed = bool(hard_failures)
        review = False

        def reason(code, severity="review"):
            nonlocal review, failed
            reasons.append(
                {
                    "code": code,
                    "severity": severity,
                    "rule_id": rule.get("rule_id", rule["platform"]),
                }
            )
            if severity == "failure":
                failed = True
            else:
                review = True

        def check(identity, passed, evidence=None):
            checks.append(
                {
                    "id": identity,
                    "status": "unverified" if not verified else ("passed" if passed else "failed"),
                    "evidence": evidence,
                }
            )
            if not passed and verified:
                reason(identity, "failure")

        for code in hard_failures:
            reason(code, "failure")
        if invalid_config:
            reason("rule_config_invalid")
        if not verified:
            reason("rule_evidence_unverified")
        if not config.complete_rule_set:
            reason("rule_set_incomplete")
        if not facts.get("analysis_complete"):
            reason("analysis_incomplete")
        if facts.get("uncertainties"):
            reason("visual_uncertainties")
        if (facts.get("image_width"), facts.get("image_height")) != (
            local.get("width"),
            local.get("height"),
        ):
            reason("facts_dimension_mismatch")
        if config.min_width is not None:
            check(
                "min_width",
                local.get("width", 0) >= config.min_width,
                {"actual": local.get("width"), "minimum": config.min_width},
            )
        if config.min_height is not None:
            check(
                "min_height",
                local.get("height", 0) >= config.min_height,
                {"actual": local.get("height"), "minimum": config.min_height},
            )
        if config.max_bytes is not None:
            check(
                "max_bytes",
                local.get("byte_size", 0) <= config.max_bytes,
                {"actual": local.get("byte_size"), "maximum": config.max_bytes},
            )
        if config.max_width is not None:
            check(
                "max_width",
                local.get("width", 0) <= config.max_width,
                {"actual": local.get("width"), "maximum": config.max_width},
            )
        if config.max_height is not None:
            check(
                "max_height",
                local.get("height", 0) <= config.max_height,
                {"actual": local.get("height"), "maximum": config.max_height},
            )
        if config.min_longest_edge is not None or config.max_longest_edge is not None:
            width, height = local.get("width"), local.get("height")
            valid = type(width) is int and type(height) is int and width > 0 and height > 0
            if not valid:
                reason("local_dimensions_invalid")
            else:
                longest = max(width, height)
                for identity, threshold, passed in (
                    (
                        "min_longest_edge",
                        config.min_longest_edge,
                        config.min_longest_edge is None or longest >= config.min_longest_edge,
                    ),
                    (
                        "max_longest_edge",
                        config.max_longest_edge,
                        config.max_longest_edge is None or longest <= config.max_longest_edge,
                    ),
                ):
                    if threshold is not None:
                        check(identity, passed, {"actual": longest, "threshold": threshold})
        if config.required_aspect_ratio is not None:
            ratio = config.required_aspect_ratio
            width, height = local.get("width"), local.get("height")
            valid = type(width) is int and type(height) is int and width > 0 and height > 0
            check(
                "aspect_ratio",
                valid and width * ratio.height == height * ratio.width,
                {
                    "actual_width": width,
                    "actual_height": height,
                    "required_width": ratio.width,
                    "required_height": ratio.height,
                },
            )
        if config.require_white_background:
            background = facts.get("background", {})
            if (
                background.get("confidence", 0) < config.minimum_confidence
                or background.get("classification") == "unknown"
            ):
                reason("background_uncertain")
            else:
                check("white_background", background.get("classification") == "white", background)
        for group, forbid in [
            ("text_items", config.forbid_added_text),
            ("logo_items", config.forbid_added_text),
            ("watermark_items", config.forbid_added_watermarks),
        ]:
            for mark in facts.get(group, []):
                origin = mark.get("origin", "unknown")
                if mark.get("confidence", 0) < config.minimum_confidence or origin == "unknown":
                    reason("mark_origin_uncertain")
                    continue
                prohibited = (origin == "added_overlay" and forbid) or (
                    origin == "on_product" and not config.allow_on_product_marks
                )
                check(
                    "added_watermark" if group == "watermark_items" else "added_text_or_logo",
                    not prohibited,
                    {"origin": origin, "bbox": mark.get("bbox")},
                )
        if config.min_product_long_edge_ratio is not None:
            extent = facts.get("product_extent")
            if not extent or extent.get("confidence", 0) < config.minimum_confidence:
                reason("product_extent_uncertain")
            else:
                x1, y1, x2, y2 = extent["bbox"]
                ratio = max(x2 - x1, y2 - y1) / max(local["width"], local["height"])
                check(
                    "product_long_edge_ratio",
                    ratio >= config.min_product_long_edge_ratio,
                    {"actual": ratio, "minimum": config.min_product_long_edge_ratio},
                )
        if config.require_identity_review:
            reason("product_identity_requires_review")
        if sampling_selected:
            reason("deterministic_sampling_review")
        status = "non_compliant" if failed else "manual_review" if review else "compliant"
        results.append(
            {
                "platform": rule["platform"],
                "marketplace": rule.get("marketplace", ""),
                "category": rule.get("category", ""),
                "image_type": rule.get("image_type", ""),
                "rule_version": rule.get("version", "unverified"),
                "evidence_status": rule.get("evidence_status", "unverified"),
                "status": status,
                "reasons": reasons,
                "checks": checks,
            }
        )
    overall = (
        "non_compliant"
        if any(p["status"] == "non_compliant" for p in results)
        else "compliant"
        if results and all(p["status"] == "compliant" for p in results)
        else "manual_review"
    )
    return {
        "final_sha256": final_sha256,
        "per_platform": results,
        "auto_overall": overall,
        "sampling_selected": sampling_selected,
    }


def evaluate(
    final_sha256, targets, *, evidence_verified=False, analysis_complete=False, hard_failures=()
):
    rules = [
        {
            "platform": p,
            "evidence_status": "verified" if evidence_verified else "unverified",
            "config": {},
        }
        for p in targets
    ]
    return evaluate_rules(
        final_sha256,
        rules,
        {"analysis_complete": analysis_complete},
        {},
        hard_failures=hard_failures,
    )
