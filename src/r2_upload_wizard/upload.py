# src/r2_upload_wizard/upload.py
from __future__ import annotations

import mimetypes
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

from r2_upload_wizard.models import UploadItem, UploadResult

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


def _upload_one(
    client,
    bucket: str,
    item: UploadItem,
    on_progress: Callable[[UploadItem], None],
) -> None:
    def callback(bytes_transferred: int) -> None:
        item.bytes_sent += bytes_transferred
        on_progress(item)

    extra_args = {}
    content_type = guess_content_type(item.local_path.name)
    if content_type:
        extra_args["ContentType"] = content_type

    item.status = "uploading"
    try:
        client.upload_file(
            str(item.local_path),
            bucket,
            item.key,
            ExtraArgs=extra_args or None,
            Callback=callback,
            Config=TRANSFER_CONFIG,
        )
    except ClientError as exc:
        item.status = "failed"
        item.error = str(exc)
    else:
        item.status = "done"
    on_progress(item)


def run(
    client,
    bucket: str,
    items: list[UploadItem],
    on_progress: Callable[[UploadItem], None],
    cancel_event: threading.Event,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> UploadResult:
    """Upload all 'pending' items with bounded concurrency, honoring cancellation.

    Items already marked 'skipped' are reported via on_progress but not
    uploaded. Once cancel_event is set, no new uploads are scheduled, but
    uploads already in flight are allowed to finish. Failures are recorded
    per item without aborting the rest of the batch.
    """
    start = time.monotonic()
    to_upload = [item for item in items if item.status == "pending"]
    for item in items:
        if item.status == "skipped":
            on_progress(item)

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        initial = to_upload[:max_workers] if not cancel_event.is_set() else []
        futures = {
            pool.submit(_upload_one, client, bucket, item, on_progress): item for item in initial
        }
        next_index = len(initial)
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                del futures[future]
                if not cancel_event.is_set() and next_index < len(to_upload):
                    item = to_upload[next_index]
                    next_index += 1
                    futures[pool.submit(_upload_one, client, bucket, item, on_progress)] = item

    succeeded = sum(1 for item in items if item.status == "done")
    skipped = sum(1 for item in items if item.status == "skipped")
    failed = [item for item in items if item.status == "failed"]
    total_bytes = sum(item.size for item in items if item.status == "done")
    return UploadResult(
        succeeded=succeeded,
        skipped=skipped,
        failed=failed,
        total_bytes=total_bytes,
        elapsed_seconds=time.monotonic() - start,
    )
