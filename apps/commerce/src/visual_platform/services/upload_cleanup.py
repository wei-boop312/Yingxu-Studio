"""Reclaim only a database-authorized, expired exact temporary upload key."""

from uuid import UUID

import psycopg

from visual_platform.db.result import required_row
from visual_platform.services.rule_operations import assert_operator
from visual_platform.storage.s3 import StorageError


def list_expired_uploads(dsn, user, limit=100):
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        return required_row(connection.execute("SELECT ops.expired_uploads(%s,%s)", (user, limit)))[
            0
        ]


def object_versions(storage, key):
    versions = []
    marker: dict[str, str] = {}
    pages = 0
    while True:
        pages += 1
        if pages > 10:
            raise StorageError("cleanup_page_limit")
        response = storage.client.list_object_versions(
            Bucket=storage.bucket, Prefix=key, MaxKeys=1000, **marker
        )
        for item in [*response.get("Versions", []), *response.get("DeleteMarkers", [])]:
            if item.get("Key") != key:
                continue
            version = item.get("VersionId")
            if (
                not isinstance(version, str)
                or not version
                or len(version) > 1024
                or any(ord(c) < 32 for c in version)
            ):
                raise StorageError("cleanup_version_id_required")
            versions.append(version)
            if len(versions) > 1000:
                raise StorageError("cleanup_version_limit")
        if not response.get("IsTruncated", False):
            break
        next_marker = {
            "KeyMarker": response.get("NextKeyMarker"),
            "VersionIdMarker": response.get("NextVersionIdMarker"),
        }
        if (
            any(not isinstance(v, str) or not v for v in next_marker.values())
            or next_marker == marker
        ):
            raise StorageError("cleanup_pagination_invalid")
        marker = next_marker
    if len(versions) != len(set(versions)):
        raise StorageError("cleanup_duplicate_version")
    return versions


def reclaim_upload(dsn, storage, user: UUID, intent: UUID, operation: UUID, reason: str):
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        claim = required_row(
            connection.execute(
                "SELECT ops.claim_upload_cleanup(%s,%s,%s,%s)", (user, intent, operation, reason)
            )
        )[0]
    if claim["state"] == "done":
        return {
            "result": "UPLOAD_TEMP_RECLAIMED",
            "removed_versions": claim["removed_versions"],
            "replayed": True,
            "paid_calls": 0,
        }
    key = claim["temp_key"]
    if key != "temp/" + str(user) + "/" + str(intent):
        raise ValueError("cleanup_key_scope_mismatch")
    versions = object_versions(storage, key)
    # Enumerate fully before deleting anything; never use prefix deletion.
    for version in versions:
        storage.client.delete_object(Bucket=storage.bucket, Key=key, VersionId=version)
    if object_versions(storage, key):
        raise StorageError("cleanup_versions_remain")
    # Versions API must not hide an unversioned current object.
    try:
        storage.client.head_object(Bucket=storage.bucket, Key=key)
    except Exception as error:
        from botocore.exceptions import ClientError

        if not isinstance(error, ClientError) or error.response.get("Error", {}).get(
            "Code"
        ) not in ("404", "NoSuchKey", "NotFound"):
            raise
    else:
        raise StorageError("cleanup_current_object_remains")
    with psycopg.connect(dsn) as connection:
        assert_operator(connection)
        connection.execute(
            "SELECT ops.finish_upload_cleanup(%s,%s,%s,%s,%s)",
            (user, intent, operation, claim["attempt"], len(versions)),
        )
    return {
        "result": "UPLOAD_TEMP_RECLAIMED",
        "removed_versions": len(versions),
        "replayed": False,
        "paid_calls": 0,
    }
