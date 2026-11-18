import hashlib
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image

from visual_platform.providers.contract import ProviderUncertain
from visual_platform.providers.fake import DurableFakeProvider


class LateFakeTests(unittest.TestCase):
    def test_lost_generate_response_preserves_queryable_receipt_without_resubmit(self):
        with tempfile.TemporaryDirectory() as folder:
            identity = uuid4()
            late = DurableFakeProvider(Path(folder), mode="late")
            generation = {"output": {"width": 12, "height": 10, "format": "png"}, "type": "scene"}
            with self.assertRaises(ProviderUncertain):
                late.submit_generate(identity, generation)
            path = Path(folder) / ("generate-" + str(identity) + ".json")
            raw = path.read_bytes()
            receipt = json.loads(raw)
            restarted = DurableFakeProvider(Path(folder))
            image = restarted.fetch_image(receipt["provider_id"])
            self.assertEqual(hashlib.sha256(image).hexdigest(), receipt["sha256"])
            with self.assertRaises(ProviderUncertain):
                restarted.submit_generate(identity, generation)
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(len(list(Path(folder).glob("generate-*.intent"))), 1)

    def test_lost_detection_response_preserves_facts_bound_to_final_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            stream = BytesIO()
            Image.new("RGB", (12, 10), "white").save(stream, "PNG")
            image = stream.getvalue()
            identity = uuid4()
            late = DurableFakeProvider(Path(folder), mode="late")
            with self.assertRaises(ProviderUncertain):
                late.submit_detect(identity, image)
            record = json.loads((Path(folder) / ("detect-" + str(identity) + ".json")).read_text())
            facts = DurableFakeProvider(Path(folder)).fetch_facts(record["provider_id"])
            self.assertEqual(facts["final_sha256"], hashlib.sha256(image).hexdigest())
            self.assertFalse(facts["facts"]["analysis_complete"])

    def test_detection_only_unknown_preserves_generation_and_refuses_duplicate_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            provider = DurableFakeProvider(directory, mode="detect_uncertain")
            generation = {"output": {"width": 12, "height": 10, "format": "png"}, "type": "scene"}
            generated = provider.submit_generate(uuid4(), generation)
            image = provider.fetch_image(generated.provider_id)
            identity = uuid4()
            with self.assertRaises(ProviderUncertain):
                provider.submit_detect(identity, image)
            self.assertFalse((directory / ("detect-" + str(identity) + ".json")).exists())
            with self.assertRaises(ProviderUncertain):
                DurableFakeProvider(directory).submit_detect(identity, image)
            self.assertEqual(len(list(directory.glob("detect-*.intent"))), 1)
            self.assertEqual(provider.fetch_image(generated.provider_id), image)
