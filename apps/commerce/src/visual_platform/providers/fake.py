import hashlib
import json
from io import BytesIO
from pathlib import Path
from uuid import UUID

from PIL import Image, ImageDraw

from visual_platform.domain.image_limits import MAX_CANONICAL_PIXELS, MAX_OUTPUT_EDGE
from visual_platform.providers.contract import ProviderFailure, ProviderUncertain, Receipt


class FakeProvider:
    """No network transport; unknown submission is deliberately never retried."""

    def __init__(self):
        self.calls = {}
        self.results = {}

    def submit(self, intent_id, mode="success"):
        if intent_id in self.calls:
            raise RuntimeError("submission already attempted")
        if mode not in ("success", "failed", "uncertain", "late"):
            raise ValueError("unknown mode")
        self.calls[intent_id] = 1
        self.results[intent_id] = mode
        return {"status": mode, "provider_id": intent_id if mode in ("success", "late") else None}

    def poll(self, provider_id):
        state = self.results[provider_id]
        return "succeeded" if state in ("success", "late") else state


class DurableFakeProvider:
    """Local-only supplier simulator. Exclusive intent files survive Worker restarts."""

    def __init__(self, directory: Path, mode="success"):
        if mode not in ("success", "failed", "uncertain", "late", "detect_uncertain"):
            raise ValueError("invalid fake mode")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.mode = mode

    def _paths(self, step, identity):
        identity = UUID(str(identity))
        key = step + "-" + str(identity)
        return (
            key,
            self.directory / (key + ".intent"),
            self.directory / (key + ".json"),
            self.directory / (key + ".png"),
        )

    def _begin(self, step, identity):
        paths = self._paths(step, identity)
        try:
            with paths[1].open("x", encoding="utf-8") as f:
                f.write(self.mode)
        except FileExistsError:
            raise ProviderUncertain("duplicate submit refused")
        if self.mode == "failed":
            raise ProviderFailure("explicit fake failure")
        if self.mode == "uncertain" or (self.mode == "detect_uncertain" and step == "detect"):
            raise ProviderUncertain("fake unknown outcome")
        return paths

    def submit_generate(self, intent_id, generation):
        size = (generation["output"]["width"], generation["output"]["height"])
        if (
            any(type(edge) is not int or not 1 <= edge <= MAX_OUTPUT_EDGE for edge in size)
            or size[0] * size[1] > MAX_CANONICAL_PIXELS
        ):
            raise ProviderFailure("fake output exceeds processing budget")
        key, _, meta, png = self._begin("generate", intent_id)
        image = Image.new(
            "RGB", size, "white" if generation["type"] == "white_background" else (224, 234, 242)
        )
        if generation["type"] != "white_background":
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, size[1] * 2 // 3, size[0], size[1]), fill=(191, 210, 204))
        stream = BytesIO()
        image.save(stream, "PNG")
        data = stream.getvalue()
        png.write_bytes(data)
        record = {"provider_id": key, "sha256": hashlib.sha256(data).hexdigest(), "offline": True}
        encoded = json.dumps(record, sort_keys=True)
        meta.write_text(encoded, encoding="utf-8")
        if self.mode == "late":
            raise ProviderUncertain("fake response lost; persisted result can be reconciled later")
        return Receipt(key, hashlib.sha256(encoded.encode()).hexdigest())

    def _read(self, provider_id):
        step, identity = provider_id.split("-", 1)
        if step not in ("generate", "detect"):
            raise ValueError("invalid fake provider id")
        key, _, meta, png = self._paths(step, identity)
        return json.loads(meta.read_text(encoding="utf-8")), png

    def fetch_image(self, provider_id):
        record, png = self._read(provider_id)
        data = png.read_bytes()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("fake artifact hash mismatch")
        return data

    def submit_detect(self, intent_id, final_image):
        key, _, meta, _ = self._begin("detect", intent_id)
        with Image.open(BytesIO(final_image)) as im:
            width, height = im.size
        facts = {
            "schema_version": 1,
            "analysis_complete": False,
            "image_width": width,
            "image_height": height,
            "text_items": [],
            "logo_items": [],
            "watermark_items": [],
            "background": {"classification": "unknown", "confidence": 0.0},
            "product_extent": None,
            "uncertainties": ["offline_fake_has_no_visual_recognition"],
        }
        record = {
            "provider_id": key,
            "final_sha256": hashlib.sha256(final_image).hexdigest(),
            "facts": facts,
            "offline": True,
        }
        encoded = json.dumps(record, sort_keys=True)
        meta.write_text(encoded, encoding="utf-8")
        if self.mode == "late":
            raise ProviderUncertain("fake response lost; persisted result can be reconciled later")
        return Receipt(key, hashlib.sha256(encoded.encode()).hexdigest())

    def fetch_facts(self, provider_id):
        return self._read(provider_id)[0]
