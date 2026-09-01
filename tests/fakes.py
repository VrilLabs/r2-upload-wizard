# tests/fakes.py
from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from botocore.exceptions import ClientError


def _client_error(code: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class FakeS3Client:
    """An in-memory stand-in for a boto3 S3 client, shaped to match the real
    client's method signatures closely enough for r2_client.py and
    upload.py to work against it unmodified.
    """

    def __init__(self) -> None:
        self.buckets: dict[str, dict[str, int]] = {}
        self.fail_keys: set[str] = set()  # keys whose upload_file should raise

    def list_buckets(self):
        return {
            "Buckets": [
                {"Name": name, "CreationDate": datetime(2026, 1, 1, tzinfo=UTC)}
                for name in self.buckets
            ]
        }

    def head_object(self, Bucket: str, Key: str):  # noqa: N803 -- matches boto3's casing
        objects = self.buckets.get(Bucket, {})
        if Key not in objects:
            raise _client_error("404", "HeadObject")
        return {"ContentLength": objects[Key]}

    def list_objects_v2(self, Bucket: str, MaxKeys: int = 1000):  # noqa: N803
        objects = self.buckets.get(Bucket, {})
        keys = list(objects)[:MaxKeys]
        return {
            "KeyCount": len(keys),
            "IsTruncated": len(objects) > MaxKeys,
            "Contents": [{"Key": key} for key in keys],
        }

    def create_bucket(self, Bucket: str):  # noqa: N803
        if Bucket in self.buckets:
            raise _client_error("BucketAlreadyExists", "CreateBucket")
        self.buckets[Bucket] = {}

    def delete_bucket(self, Bucket: str):  # noqa: N803
        del self.buckets[Bucket]

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None, Callback=None, Config=None):  # noqa: N803
        if Key in self.fail_keys:
            raise _client_error("InternalError", "PutObject")
        size = Path(Filename).stat().st_size
        if Callback is not None:
            Callback(size)  # simulate the whole file transferring in one chunk
        time.sleep(0)  # yield, matching real threaded I/O behavior in tests
        self.buckets.setdefault(Bucket, {})[Key] = size
