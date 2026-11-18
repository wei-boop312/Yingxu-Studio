"""Validate disjoint Qwen image meters; never invent token counts or fiat spending."""

METERS = ("qima_input_1k", "qima_input_2k", "qima_output_1k", "qima_output_2k")


def image_usage(usage, *, input_count, width, height):
    """Compare supplier usage with the frozen request AND actual decoded dimensions."""
    unknown = {"billing_usage_integrity": "invalid", "quantities": None}
    if usage is None:
        return {**unknown, "billing_usage_integrity": "missing"}
    if (
        type(input_count) is not int
        or not 1 <= input_count <= 3
        or type(width) is not int
        or type(height) is not int
        or not 512 * 512 <= width * height <= 4_000_000
        or min(width, height) < 256
        or max(width, height) > 6256
        or max(width, height) > 8 * min(width, height)
        or type(usage) is not dict
        or set(usage)
        != {
            "output_width",
            "output_height",
            "input_image_count",
            "input_image_type",
            "output_image_count",
            "output_image_type",
        }
    ):
        return unknown
    for field, expected in (
        ("output_width", width),
        ("output_height", height),
        ("input_image_count", input_count),
        ("output_image_count", 1),
    ):
        if type(usage[field]) is not int or usage[field] != expected:
            return unknown
    suffix = "1k" if width * height <= 2_250_000 else "2k"
    incoming, outgoing = "qima_input_" + suffix, "qima_output_" + suffix
    if usage["input_image_type"] != incoming or usage["output_image_type"] != outgoing:
        return unknown
    quantities = dict.fromkeys(METERS, 0)
    quantities[incoming] = input_count
    quantities[outgoing] = 1
    return {"billing_usage_integrity": "complete", "quantities": quantities}
