import os
from dataclasses import dataclass
from pathlib import Path

from visual_platform.core.service_urls import validate_app_origin, validate_storage_endpoint


@dataclass(frozen=True)
class Settings:
    api_database_url: str
    auth_database_url: str
    private_key: str
    public_key: str
    origin: str
    cookie_secure: bool = True
    s3_endpoint: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "visual-private"
    s3_region: str = "us-east-1"
    issuer: str = "visual-platform"
    audience: str = "visual-web"
    provider_profile: str = "offline"
    studio_comfy_url: str | None = None

    def __post_init__(self):
        if self.studio_comfy_url:
            from visual_platform.providers.comfyui import local_url

            local_url(self.studio_comfy_url)
        if self.provider_profile not in ("offline", "bailian_live"):
            raise ValueError("unsupported_provider_profile")
        object.__setattr__(self, "origin", validate_app_origin(self.origin, self.cookie_secure))
        if self.s3_endpoint:
            validate_storage_endpoint(self.s3_endpoint)
            if not self.s3_access_key or not self.s3_secret_key:
                raise ValueError("explicit S3 credentials required")

    @classmethod
    def from_env(cls):
        cookie_mode = os.environ.get("COOKIE_SECURE", "1")
        if cookie_mode not in ("0", "1"):
            raise ValueError("invalid_cookie_security_mode")
        return cls(
            studio_comfy_url=os.environ.get("STUDIO_COMFY_URL") or None,
            provider_profile=os.environ.get("PROVIDER_PROFILE", "offline"),
            api_database_url=os.environ["API_DATABASE_URL"],
            auth_database_url=os.environ["AUTH_DATABASE_URL"],
            private_key=Path(os.environ["JWT_PRIVATE_KEY_FILE"]).read_text(),
            public_key=Path(os.environ["JWT_PUBLIC_KEY_FILE"]).read_text(),
            origin=os.environ["APP_ORIGIN"],
            cookie_secure=cookie_mode == "1",
            s3_endpoint=os.environ.get("S3_ENDPOINT"),
            s3_access_key=os.environ.get("S3_ACCESS_KEY"),
            s3_secret_key=os.environ.get("S3_SECRET_KEY"),
            s3_bucket=os.environ.get("S3_BUCKET", "visual-private"),
            s3_region=os.environ.get("S3_REGION", "us-east-1"),
        )
