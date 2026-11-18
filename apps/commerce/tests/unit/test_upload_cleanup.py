"""Exact-key version enumeration must fail before any deletion on bad inventories."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from visual_platform.services.upload_cleanup import object_versions
from visual_platform.storage.s3 import StorageError


class UploadCleanupUnitTests(unittest.TestCase):
    def storage(self, responses):
        client = Mock()
        client.list_object_versions.side_effect = responses
        return SimpleNamespace(bucket="fixture-private", client=client)

    def test_exact_key_and_delete_markers_only_with_pagination(self):
        key = "temp/user/intent"
        storage = self.storage(
            [
                {
                    "Versions": [
                        {"Key": key, "VersionId": "v1"},
                        {"Key": key + "-other", "VersionId": "never-delete"},
                    ],
                    "IsTruncated": True,
                    "NextKeyMarker": key,
                    "NextVersionIdMarker": "v1",
                },
                {
                    "DeleteMarkers": [{"Key": key, "VersionId": "marker"}],
                    "Versions": [{"Key": key, "VersionId": "v2"}],
                },
            ]
        )
        self.assertEqual(object_versions(storage, key), ["v1", "v2", "marker"])
        storage.client.delete_object.assert_not_called()
        self.assertEqual(storage.client.list_object_versions.call_args.kwargs["KeyMarker"], key)
        self.assertEqual(storage.client.list_object_versions.call_args.kwargs["Prefix"], key)

    def test_missing_duplicate_and_excess_versions_rejected_without_deletion(self):
        for entries in (
            [{"Key": "temp/key"}],
            [{"Key": "temp/key", "VersionId": "x"}] * 2,
            [{"Key": "temp/key", "VersionId": str(n)} for n in range(1001)],
        ):
            storage = self.storage([{"Versions": entries}])
            with self.assertRaises(StorageError):
                object_versions(storage, "temp/key")
            storage.client.delete_object.assert_not_called()

    def test_invalid_repeated_and_unbounded_pagination_rejected(self):
        for responses in (
            [{"IsTruncated": True}],
            [{"IsTruncated": True, "NextKeyMarker": "key", "NextVersionIdMarker": "v"}] * 2,
            [
                {"IsTruncated": True, "NextKeyMarker": str(n), "NextVersionIdMarker": str(n)}
                for n in range(11)
            ],
        ):
            storage = self.storage(responses)
            with self.assertRaises(StorageError):
                object_versions(storage, "temp/key")
            storage.client.delete_object.assert_not_called()
