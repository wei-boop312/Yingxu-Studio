import unittest

from visual_platform.core.event_replay import requires_event_reset


class EventReplayTests(unittest.TestCase):
    def test_sparse_pruning_does_not_reset_repeatedly_after_replaying_retained_older_ids(self):
        self.assertTrue(requires_event_reset(300, 100, 200, 300, 0))
        self.assertFalse(requires_event_reset(200, 100, 200, 300, 300))
        self.assertFalse(requires_event_reset(200, 100, 200, 300, 300))
        self.assertTrue(requires_event_reset(200, 100, 400, 350, 300))

    def test_initial_empty_and_out_of_range_cursors_still_follow_protocol(self):
        self.assertFalse(requires_event_reset(0, None, None, 300, 0))
        self.assertTrue(requires_event_reset(200, None, None, 300, 0))
        self.assertTrue(requires_event_reset(99, 100, 200, 0, 0))
        self.assertTrue(requires_event_reset(201, 100, 200, 0, 0))
        self.assertFalse(requires_event_reset(200, 100, 200, 0, 0))

    def test_bigint_cursor_comparisons_preserve_exact_precision(self):
        high = 9007199254740993
        self.assertTrue(requires_event_reset(high - 1, 1, high, high - 1, 0))
        self.assertFalse(requires_event_reset(high, 1, high, high - 1, high - 1))
