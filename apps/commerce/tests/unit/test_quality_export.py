"""Annotation exports preserve source bytes and never manufacture human labels."""

import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from PIL import Image

from visual_platform.services.quality_evaluation import evaluate_dataset
from visual_platform.services.quality_export import (
    export_quality_pack,
    prepare_sample,
    private_destination,
)


class QualityExportTests(unittest.TestCase):
    def setUp(self):
        stream = io.BytesIO()
        Image.new("RGB", (32, 32), "white").save(stream, "PNG")
        self.image = stream.getvalue()
        self.entry = {
            "platform": "amazon",
            "marketplace": "US",
            "category": "generic",
            "image_type": "MAIN",
            "rule_version": "fixture",
            "status": "manual_review",
        }
        self.report = {
            "final_sha256": hashlib.sha256(self.image).hexdigest(),
            "facts": None,
            "per_platform": [self.entry],
            "provider_profile": "offline_fake",
        }
        raw = json.dumps(self.report).encode()
        self.context = {
            "final": {
                "bucket": "private-fixture",
                "object_key": "private/final",
                "storage_version_id": "image-version",
                "sha256": self.report["final_sha256"],
                "width": 32,
                "height": 32,
                "byte_size": len(self.image),
            },
            "report": {
                "bucket": "private-fixture",
                "object_key": "private/report",
                "storage_version_id": "report-version",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "facts": None,
                "per_platform": [self.entry],
            },
        }
        self.objects = {
            "private/final": (self.image, "image/png", "image-version"),
            "private/report": (raw, "application/json", "report-version"),
        }
        objects = self.objects

        class Storage:
            bucket = "private-fixture"

            def get(self, key, *, version, limit):
                data, mime, stored_version = objects[key]
                if len(data) > limit:
                    raise ValueError("fixture size limit")
                return data, mime, stored_version

        self.storage = Storage()

    def test_blank_pack_preserves_bytes_and_cannot_be_evaluated_as_labelled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "fixture"
            with patch(
                "visual_platform.services.quality_export.quality_export_context",
                return_value=self.context,
            ):
                result = export_quality_pack(
                    "unused-offline",
                    self.storage,
                    uuid4(),
                    uuid4(),
                    uuid4(),
                    target,
                    root,
                    "synthetic_fixture",
                )
            self.assertFalse(result["human_labels_complete"])
            self.assertEqual((target / "final.png").read_bytes(), self.image)
            self.assertEqual(
                (target / "report.json").read_bytes(), self.objects["private/report"][0]
            )
            document = json.loads((target / "manifest.json").read_bytes())
            self.assertFalse(document["human_reviewed_attested"])
            self.assertFalse(document["usage_rights_attested"])
            row = document["samples"][0]
            self.assertIsNone(row["human_label"])
            self.assertIsNone(row["annotator_fingerprint"])
            self.assertTrue(all(value is None for value in row["features"].values()))
            with self.assertRaises(ValueError):
                evaluate_dataset(target / "manifest.json")
            public = json.dumps(result)
            self.assertNotIn("private/", public)
            self.assertNotIn("private-fixture", public)

    def test_digest_version_size_and_tenant_bucket_binding_are_required(self):
        for section, field, value in (
            ("final", "sha256", "0" * 64),
            ("report", "sha256", "0" * 64),
            ("final", "storage_version_id", "wrong"),
            ("report", "storage_version_id", "wrong"),
            ("final", "byte_size", 1),
            ("report", "bucket", "another-tenant"),
        ):
            context = copy.deepcopy(self.context)
            context[section][field] = value
            with self.subTest(section=section, field=field), self.assertRaises(ValueError):
                prepare_sample(context, self.storage, "synthetic_fixture")

    def test_automatic_outcomes_and_offline_source_cannot_become_human_or_real_labels(self):
        _, _, samples, profile = prepare_sample(self.context, self.storage, "synthetic_fixture")
        self.assertEqual(profile, "offline_fake")
        self.assertIsNone(samples[0]["human_label"])
        with self.assertRaisesRegex(ValueError, "source_mismatch"):
            prepare_sample(self.context, self.storage, "real_product_set")
        context = copy.deepcopy(self.context)
        context["report"]["per_platform"][0]["category"] = "changed"
        with self.assertRaisesRegex(ValueError, "binding_mismatch"):
            prepare_sample(context, self.storage, "synthetic_fixture")

    def test_private_destination_requires_fresh_directory_and_rejects_escape_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            existing = root / "existing"
            existing.mkdir()
            for path in (existing, root.parent / "outside", root / "../outside", root / "bad.name"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    private_destination(path, root)
            self.assertEqual(private_destination(root / "fresh", root), root / "fresh")
            link = root / "linked"
            try:
                link.symlink_to(existing, target_is_directory=True)
            except OSError:
                return  # Windows directory symlinks may require an unavailable local privilege.
            with self.assertRaises(ValueError):
                private_destination(link / "fresh", root)
