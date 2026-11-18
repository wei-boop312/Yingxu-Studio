from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class ProviderFailure(Exception):
    pass


class ProviderUncertain(Exception):
    pass


class ProviderPending(Exception):
    pass


@dataclass(frozen=True)
class Receipt:
    provider_id: str
    response_digest: str
    polling_url: str | None = None


class ImageProvider(Protocol):
    def submit_generate(self, intent_id: UUID, generation: dict) -> Receipt: ...
    def fetch_image(self, provider_id: str) -> bytes: ...
    def submit_detect(self, intent_id: UUID, final_image: bytes) -> Receipt: ...
    def fetch_facts(self, provider_id: str) -> dict: ...
