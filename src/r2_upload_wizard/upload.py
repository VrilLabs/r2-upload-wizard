# src/r2_upload_wizard/upload.py
from __future__ import annotations

import mimetypes

from boto3.s3.transfer import TransferConfig

from r2_upload_wizard.models import UploadItem

TRANSFER_CONFIG = TransferConfig(
    multipart_chunksize=64 * 1024 * 1024,
    multipart_threshold=256 * 1024 * 1024,
    max_concurrency=4,
    use_threads=True,
)

DEFAULT_MAX_WORKERS = 8


def guess_content_type(filename: str) -> str | None:
    content_type, _ = mimetypes.guess_type(filename)
    return content_type


def plan_items(
    items: list[UploadItem], existing: dict[str, int], overwrite_existing: bool
) -> list[UploadItem]:
    """Mark items whose key already exists at destination with a matching
    size as 'skipped' unless overwrite_existing is True. A size mismatch is
    treated as *not* existing (the item stays 'pending' and re-uploads),
    since a same-name/different-size file is a changed file, not a dup.
    """
    if not overwrite_existing:
        for item in items:
            if existing.get(item.key) == item.size:
                item.status = "skipped"
    return items
