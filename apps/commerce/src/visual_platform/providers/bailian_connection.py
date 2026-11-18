"""Private connection preparation, separate from registered spending/runtime authority."""

import hashlib
import hmac
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from visual_platform.providers.qwen_image import strict_document, workspace_origin
from visual_platform.providers.runtime import credential_fingerprint
from visual_platform.services.quality_export import private_destination


class BailianConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Annotated[int, Field(ge=1, le=1)] = 1
    provider: Literal["aliyun_bailian"] = "aliyun_bailian"
    model: Literal["qwen-image-3.0"] = "qwen-image-3.0"
    endpoint_origin: str
    credential_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @field_validator("endpoint_origin")
    @classmethod
    def beijing_workspace(cls, value):
        return workspace_origin(value)

    def canonical(self):
        return json.dumps(
            self.model_dump(), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()

    def digest(self):
        return hashlib.sha256(self.canonical()).hexdigest()


def prepare_connection(endpoint_origin, key, output: Path, private_root: Path):
    # Resolve destination BEFORE touching credentials or creating any output.
    destination = private_destination(output, private_root)
    configuration = BailianConnection(
        endpoint_origin=endpoint_origin, credential_fingerprint=credential_fingerprint(key)
    )
    destination.mkdir(mode=0o700)
    path = destination / "connection.json"
    import os

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(configuration.canonical())
        stream.flush()
        os.fsync(stream.fileno())
    return configuration


def load_connection(path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise ValueError("invalid_bailian_connection_file")
    with path.open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("bailian_connection_oversized")
    return BailianConnection.model_validate(strict_document(raw), strict=True)


def connection_status(configuration, key):
    configuration = BailianConnection.model_validate(configuration.model_dump(), strict=True)
    try:
        matches = hmac.compare_digest(
            configuration.credential_fingerprint, credential_fingerprint(key)
        )
    except ValueError:
        matches = False
    return {
        "result": "LOCAL_CONNECTION_REVIEW",
        "model": configuration.model,
        "configuration_sha256": configuration.digest(),
        "credential_matches": matches,
        "merchant_identity_verified": False,
        "provider_verified": False,
        "price_reviewed": False,
        "execution_enabled": False,
        "paid_model_calls": 0,
    }
