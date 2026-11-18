"""Cursor reset decisions after authorization and tenant-scoped event reads."""


def requires_event_reset(position, first, last, pruned_through, acknowledged_prune):
    return bool(
        position
        and (
            first is None
            or position < first
            or position > last
            or (position <= pruned_through and pruned_through > acknowledged_prune)
        )
    )
