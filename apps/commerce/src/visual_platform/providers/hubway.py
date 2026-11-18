"""Project-selected Gemini gateway; live compatibility still needs authorized evidence."""

import os

from visual_platform.core.credentials import private_credential
from visual_platform.providers.gemini import HUBWAY_ORIGINS, MODEL, GeminiProvider


class HubwayGeminiProvider(GeminiProvider):
    def __init__(self, key, private_directory, *, transport=None, base_url="https://hubway.cc"):
        if base_url not in HUBWAY_ORIGINS:
            raise ValueError("Hubway endpoint must be the default or direct origin")
        super().__init__(key, private_directory, transport=transport, base_url=base_url)

    @classmethod
    def from_environment(cls, private_directory, *, transport=None):
        key = private_credential("HUBWAY_API_KEY")
        if not key:
            raise ValueError("HUBWAY_API_KEY required in private server environment")
        if os.environ.get("HUBWAY_VISION_MODEL", MODEL) != MODEL:
            raise ValueError("unreviewed Hubway model substitution rejected")
        return cls(
            key,
            private_directory,
            transport=transport,
            base_url=os.environ.get("HUBWAY_BASE_URL", "https://hubway.cc"),
        )
