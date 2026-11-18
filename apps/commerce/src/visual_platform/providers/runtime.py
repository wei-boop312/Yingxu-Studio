"""Explicit provider bindings and offline composition; no live activation or fallback."""

import hashlib
import hmac
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from visual_platform.core.credentials import private_credential
from visual_platform.providers.bfl import BflProvider
from visual_platform.providers.gemini import HUBWAY_ORIGINS, MODEL, OFFICIAL_ORIGIN, GeminiProvider
from visual_platform.providers.gemini_usage import unique_object
from visual_platform.providers.hubway import HubwayGeminiProvider
from visual_platform.providers.pipeline import ProtocolProvider
from visual_platform.providers.qwen_image import ASYNC_PATH, QwenImageProvider, workspace_origin
from visual_platform.providers.qwen_pipeline import (
    DualBailianProtocolProvider,
    QwenProtocolProvider,
)
from visual_platform.providers.qwen_vision import MODEL as VISION_MODEL
from visual_platform.providers.qwen_vision import PATH as VISION_PATH
from visual_platform.providers.qwen_vision import QwenVisionProvider


class RuntimeStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    provider: Literal[
        "bfl", "google_gemini", "hubway_gemini", "aliyun_bailian", "aliyun_bailian_vision"
    ]
    model: str
    endpoint_origin: str
    merchant_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    credential_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    billing_group: Annotated[str, Field(min_length=1, max_length=160)]

    @field_validator("billing_group")
    @classmethod
    def plain_group(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("invalid_runtime_billing_group")
        return value

    @model_validator(mode="after")
    def identity(self):
        if self.provider in ("aliyun_bailian", "aliyun_bailian_vision"):
            workspace_origin(self.endpoint_origin)
            if self.model != (
                "qwen-image-3.0" if self.provider == "aliyun_bailian" else VISION_MODEL
            ):
                raise ValueError("unreviewed_runtime_identity")
            return self
        allowed = {
            "bfl": ("flux-3-image", {"https://api.bfl.ai"}),
            "google_gemini": (MODEL, {OFFICIAL_ORIGIN}),
            "hubway_gemini": (MODEL, HUBWAY_ORIGINS),
        }
        model, origins = allowed[self.provider]
        if self.model != model or self.endpoint_origin not in origins:
            raise ValueError("unreviewed_runtime_identity")
        return self

    @property
    def key_environment(self):
        return {
            "bfl": "BFL_API_KEY",
            "google_gemini": "GEMINI_API_KEY",
            "hubway_gemini": "HUBWAY_API_KEY",
            "aliyun_bailian": "DASHSCOPE_API_KEY",
            "aliyun_bailian_vision": "DASHSCOPE_API_KEY",
        }[self.provider]


class ProviderRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Annotated[int, Field(ge=1, le=3)]
    version: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")]
    generate: RuntimeStep
    detect: RuntimeStep

    @model_validator(mode="after")
    def stages(self):
        expected_generator = "bfl" if self.schema_version == 1 else "aliyun_bailian"
        expected_detectors = (
            ("aliyun_bailian_vision",)
            if self.schema_version == 3
            else ("google_gemini", "hubway_gemini")
        )
        if (
            self.generate.provider != expected_generator
            or self.detect.provider not in expected_detectors
        ):
            raise ValueError("unsupported_runtime_stage")
        if self.schema_version == 3 and any(
            getattr(self.generate, field) != getattr(self.detect, field)
            for field in (
                "endpoint_origin",
                "credential_fingerprint",
                "merchant_fingerprint",
                "billing_group",
            )
        ):
            raise ValueError("dual_bailian_requires_same_workspace_and_account")
        return self

    def canonical(self):
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()

    def digest(self):
        return hashlib.sha256(self.canonical()).hexdigest()

    def execution_binding(self):
        return {
            "provider_runtime_sha256": self.digest(),
            "provider_models": {
                name: step.model
                for name, step in (("generate", self.generate), ("detect", self.detect))
            },
            "provider_endpoints": {
                "generate": self.generate.endpoint_origin
                + (ASYNC_PATH if self.schema_version >= 2 else "/v1/flux-3-image"),
                "detect": self.detect.endpoint_origin
                + (
                    VISION_PATH
                    if self.schema_version == 3
                    else "/v1beta/models/" + MODEL + ":generateContent"
                ),
            },
            "provider_accounts": {
                name: {
                    "merchant_fingerprint": step.merchant_fingerprint,
                    "billing_group": step.billing_group,
                }
                for name, step in (("generate", self.generate), ("detect", self.detect))
            },
        }


def load_runtime(path: Path):
    def invalid(value):
        raise ValueError("nonstandard_runtime_json")

    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise ValueError("invalid_runtime_configuration")
    with path.open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("invalid_runtime_configuration")
    return ProviderRuntime.model_validate(
        json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid), strict=True
    )


def credential_fingerprint(key):
    if not isinstance(key, str) or re.fullmatch(r"[\x21-\x7e]{1,4096}", key) is None:
        raise ValueError("invalid_runtime_credential")
    return hashlib.sha256(b"visual-provider-credential-v1\x00" + key.encode("ascii")).hexdigest()


def credential_status(runtime: ProviderRuntime, environment=None):
    result = {}
    for name, step in (("generate", runtime.generate), ("detect", runtime.detect)):
        key = private_credential(step.key_environment, environment)
        try:
            result[name] = hmac.compare_digest(
                credential_fingerprint(key), step.credential_fingerprint
            )
        except ValueError:
            result[name] = False
    return result


@asynccontextmanager
async def offline_runtime(
    runtime: ProviderRuntime,
    directory: Path,
    *,
    generate_key,
    detect_key,
    generate_transport,
    detect_transport,
):
    if not isinstance(generate_transport, httpx.MockTransport) or not isinstance(
        detect_transport, httpx.MockTransport
    ):
        raise ValueError("runtime_composition_requires_two_explicit_mock_transports")
    # Round-trip validates instances produced by model_copy(update=...), which bypasses Pydantic.
    runtime = ProviderRuntime.model_validate(runtime.model_dump(mode="json"), strict=True)
    if not (
        hmac.compare_digest(
            credential_fingerprint(generate_key), runtime.generate.credential_fingerprint
        )
        and hmac.compare_digest(
            credential_fingerprint(detect_key), runtime.detect.credential_fingerprint
        )
    ):
        raise ValueError("runtime_credential_mismatch")
    bfl = (
        QwenImageProvider(
            generate_key,
            directory / "bailian",
            endpoint_origin=runtime.generate.endpoint_origin,
            transport=generate_transport,
        )
        if runtime.schema_version >= 2
        else BflProvider(generate_key, directory / "bfl", transport=generate_transport)
    )
    gemini: GeminiProvider | QwenVisionProvider | None = None
    try:
        if runtime.schema_version == 3:
            gemini = QwenVisionProvider(
                detect_key,
                directory / "qwen-vision",
                transport=detect_transport,
                endpoint_origin=runtime.detect.endpoint_origin,
            )
        else:
            detector = (
                HubwayGeminiProvider
                if runtime.detect.provider == "hubway_gemini"
                else GeminiProvider
            )
            gemini = detector(
                detect_key,
                directory / "gemini",
                transport=detect_transport,
                base_url=runtime.detect.endpoint_origin,
            )
        composition = (
            DualBailianProtocolProvider
            if runtime.schema_version == 3
            else QwenProtocolProvider
            if runtime.schema_version == 2
            else ProtocolProvider
        )
        yield composition(
            bfl,
            gemini,
            merchant_accounts=runtime.execution_binding()["provider_accounts"],
            runtime_sha256=runtime.digest(),
        )
    finally:
        bfl.close()
        if gemini is not None:
            await gemini.aclose()
