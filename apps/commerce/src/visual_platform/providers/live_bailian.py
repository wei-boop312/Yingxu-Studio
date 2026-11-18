"""Explicit live composition. No account-wide spend ceiling is inferred."""

import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from visual_platform.providers.fake import DurableFakeProvider
from visual_platform.providers.generation_archive import generation_observation
from visual_platform.providers.qwen_image import QwenImageProvider
from visual_platform.providers.qwen_pipeline import DualBailianProtocolProvider
from visual_platform.providers.qwen_vision import QwenVisionProvider
from visual_platform.providers.runtime import ProviderRuntime, credential_fingerprint
from visual_platform.worker.runner import Worker


class LiveBailianProvider(DualBailianProtocolProvider):
    execution_profile = "bailian_live"
    report_profile = "bailian_live"
    allow_live_transport = True

    def __init__(self, qwen, vision, *, merchant_accounts, runtime_sha256):
        if qwen.offline_transport or vision.offline_transport:
            raise ValueError("live_composition_requires_two_explicit_live_adapters")
        super().__init__(
            qwen, vision, merchant_accounts=merchant_accounts, runtime_sha256=runtime_sha256
        )

    @staticmethod
    def observation_from_generation(record):
        return {**generation_observation(record), "transport": "bailian_live"}

    @staticmethod
    def observation_from_facts(record):
        return {
            **DualBailianProtocolProvider.observation_from_facts(record),
            "transport": "bailian_live",
        }

    def observation(self, step, attempt):
        return {**super().observation(step, attempt), "transport": "bailian_live"}


@asynccontextmanager
async def live_bailian_runtime(runtime: ProviderRuntime, directory: Path, *, key):
    runtime = ProviderRuntime.model_validate(runtime.model_dump(mode="json"), strict=True)
    if runtime.schema_version != 3 or not hmac.compare_digest(
        credential_fingerprint(key), runtime.generate.credential_fingerprint
    ):
        raise ValueError("explicit_matching_dual_bailian_runtime_required")
    if directory.is_symlink() or directory.is_junction():
        raise ValueError("invalid_live_provider_directory")
    image = QwenImageProvider(
        key, directory / "generation", endpoint_origin=runtime.generate.endpoint_origin, live=True
    )
    vision = None
    try:
        vision = QwenVisionProvider(
            key, directory / "detection", endpoint_origin=runtime.detect.endpoint_origin, live=True
        )
        yield LiveBailianProvider(
            image,
            vision,
            merchant_accounts=runtime.execution_binding()["provider_accounts"],
            runtime_sha256=runtime.digest(),
        )
    finally:
        image.close()
        if vision is not None:
            await vision.aclose()


class LiveRoutingWorker:
    """One consumer supports old offline tasks and explicitly authorized live tasks."""

    validation_profile = "bailian_live"

    def __init__(self, pool, storage, provider, fake_directory):
        self.live = Worker(pool, storage, provider)
        self.offline = Worker(pool, storage, DurableFakeProvider(fake_directory))
        self.pool = pool

    async def db(self, query, *args):
        return await self.live.db(query, *args)

    async def run(self, task):
        profile = await self.db("SELECT ops.task_provider_profile($1)", task)
        if profile == "bailian_live":
            return await self.live.run(task)
        if profile == "offline":
            return await self.offline.run(task)
        return "not_claimed"
