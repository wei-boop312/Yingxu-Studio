"""Synthetic keys only; tests never inspect real user environment credentials."""

import unittest
from unittest.mock import patch

from visual_platform.core.credentials import private_credential


class PrivateCredentialTests(unittest.TestCase):
    def test_bailian_key_uses_the_same_explicit_override_boundary(self):
        with patch(
            "visual_platform.core.credentials._windows_user_credential",
            side_effect=AssertionError("must not inspect a real user credential"),
        ):
            self.assertEqual(private_credential("DASHSCOPE_API_KEY", {"DASHSCOPE_API_KEY": ""}), "")
            self.assertEqual(
                private_credential("DASHSCOPE_API_KEY", {"DASHSCOPE_API_KEY": "synthetic"}),
                "synthetic",
            )
            self.assertIsNone(private_credential("DASHSCOPE_API_KEY", {}))

    def test_explicit_environment_and_blank_override_never_fall_back_to_an_old_key(self):
        with patch(
            "visual_platform.core.credentials._windows_user_credential",
            side_effect=AssertionError("user store must not be read"),
        ):
            self.assertEqual(
                private_credential("BFL_API_KEY", {"BFL_API_KEY": "synthetic-process-key"}),
                "synthetic-process-key",
            )
            self.assertEqual(private_credential("BFL_API_KEY", {"BFL_API_KEY": ""}), "")
            self.assertIsNone(private_credential("BFL_API_KEY", {}))

    def test_current_process_takes_precedence_and_missing_key_can_use_selected_user_store(self):
        with (
            patch.dict("os.environ", {"BFL_API_KEY": ""}, clear=True),
            patch("visual_platform.core.credentials._windows_user_credential") as store,
        ):
            self.assertEqual(private_credential("BFL_API_KEY"), "")
            store.assert_not_called()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "visual_platform.core.credentials._windows_user_credential",
                return_value="synthetic-user-key",
            ) as store,
        ):
            self.assertEqual(private_credential("HUBWAY_API_KEY"), "synthetic-user-key")
            store.assert_called_once_with("HUBWAY_API_KEY")

    def test_arbitrary_environment_names_cannot_be_requested(self):
        with patch(
            "visual_platform.core.credentials._windows_user_credential",
            side_effect=AssertionError("must not read"),
        ):
            with self.assertRaises(ValueError):
                private_credential("UNRELATED_PRIVATE_SECRET", {})
