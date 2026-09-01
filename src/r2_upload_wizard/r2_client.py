from __future__ import annotations

import re

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from r2_upload_wizard.models import BucketInfo

__all__ = [
    "BucketInfo",
    "R2Error",
    "build_client",
    "list_buckets",
    "head_object_size",
    "validate_bucket_name",
    "BucketAlreadyExistsError",
    "BucketNotEmptyError",
    "is_bucket_empty",
    "create_bucket",
    "delete_bucket",
]

_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?$")


class R2Error(Exception):
    """Base class for R2-specific errors raised by this module."""


class BucketAlreadyExistsError(R2Error):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"bucket name '{name}' is already taken")


class BucketNotEmptyError(R2Error):
    def __init__(self, approx_count: str):
        self.approx_count = approx_count
        super().__init__(f"bucket is not empty ({approx_count} object(s))")


def build_client(account_id: str, access_key_id: str, secret_access_key: str, s3_url: str):
    """Build a boto3 S3 client pointed at an R2 account's S3-compatible endpoint.

    `account_id` isn't passed to boto3 directly (the endpoint URL already
    encodes it) but is accepted here so callers can pass the full env-var
    set uniformly; keeping the parameter also makes intent explicit at call
    sites and leaves room for future Cloudflare-API-based features.
    """
    del account_id  # not needed by boto3 itself; see docstring
    return boto3.client(
        "s3",
        endpoint_url=s3_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
        config=Config(
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def list_buckets(client) -> list[BucketInfo]:
    response = client.list_buckets()
    return [
        BucketInfo(name=bucket["Name"], creation_date=bucket.get("CreationDate"))
        for bucket in response.get("Buckets", [])
    ]


def head_object_size(client, bucket: str, key: str) -> int | None:
    """Return the object's size in bytes, or None if it doesn't exist."""
    try:
        response = client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return response["ContentLength"]


def validate_bucket_name(name: str) -> str | None:
    if not (3 <= len(name) <= 63):
        return "must be 3-63 characters"
    if not _NAME_RE.match(name):
        return "lowercase letters, digits, hyphens only; must start/end alphanumeric"
    return None


def is_bucket_empty(client, bucket: str) -> tuple[bool, str]:
    response = client.list_objects_v2(Bucket=bucket, MaxKeys=1)
    count = response.get("KeyCount", len(response.get("Contents", [])))
    if count == 0:
        return True, "0"
    approx = "1+" if response.get("IsTruncated") else str(count)
    return False, approx


def create_bucket(client, name: str) -> None:
    reason = validate_bucket_name(name)
    if reason:
        raise ValueError(reason)
    try:
        client.create_bucket(Bucket=name)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
            raise BucketAlreadyExistsError(name) from exc
        raise


def delete_bucket(client, name: str) -> None:
    empty, approx_count = is_bucket_empty(client, name)
    if not empty:
        raise BucketNotEmptyError(approx_count)
    client.delete_bucket(Bucket=name)
