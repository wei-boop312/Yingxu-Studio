import hashlib

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from visual_platform.core.service_urls import validate_storage_endpoint
from visual_platform.services.telemetry import archive_observed


class StorageError(Exception):
    pass


class S3Storage:
    def __init__(self, settings):
        validate_storage_endpoint(settings.s3_endpoint)
        self.bucket = settings.s3_bucket
        params = dict(
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        self.client = boto3.client(
            "s3",
            **params,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=3,
                read_timeout=8,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
        self.probe_client = boto3.client(
            "s3",
            **params,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=0.5,
                read_timeout=0.75,
                retries={"total_max_attempts": 1},
            ),
        )

    def probe(self):
        self.probe_client.head_bucket(Bucket=self.bucket)

    def upload_url(self, key, mime, size):
        return self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": mime, "ContentLength": size},
            ExpiresIn=900,
        )

    def download_url(self, key, version=None, filename="image.png"):
        params = {
            "Bucket": self.bucket,
            "Key": key,
            "ResponseContentDisposition": 'attachment; filename="' + filename + '"',
        }
        if version:
            params["VersionId"] = version
        return self.client.generate_presigned_url("get_object", Params=params, ExpiresIn=60)

    def get(self, key, *, version=None, limit=20 * 1024 * 1024, short_probe=False):
        params = {"Bucket": self.bucket, "Key": key}
        if version:
            params["VersionId"] = version
        client = self.probe_client if short_probe else self.client
        result = client.get_object(**params)
        try:
            if result["ContentLength"] > limit:
                raise ValueError("file_size")
            data = result["Body"].read(limit + 1)
            if len(data) > limit:
                raise ValueError("file_size")
            return data, result.get("ContentType"), result.get("VersionId")
        finally:
            result["Body"].close()

    @archive_observed
    def put_immutable(self, key, data, mime):
        sha = hashlib.sha256(data).hexdigest()
        try:
            result = self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=mime,
                Metadata={"sha256": sha},
                IfNoneMatch="*",
            )
            version = result.get("VersionId")
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("PreconditionFailed", "412"):
                raise
            version = None
        stored, _, actual_version = self.get(key, version=version, limit=max(len(data), 1))
        if hashlib.sha256(stored).hexdigest() != sha:
            raise StorageError("immutable_object_conflict")
        return actual_version or version

    def close(self):
        self.client.close()
        self.probe_client.close()
