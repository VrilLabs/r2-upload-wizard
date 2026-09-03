# tests/test_upload_run.py
import threading
from pathlib import Path

from botocore.exceptions import EndpointConnectionError

from r2_upload_wizard import upload
from r2_upload_wizard.models import UploadItem
from tests.fakes import FakeS3Client


def _write_file(path: Path, content: bytes) -> UploadItem:
    path.write_bytes(content)
    return UploadItem(local_path=path, relative_path=path.name, key=path.name, size=len(content))


def test_run_uploads_all_pending_items(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    items = [_write_file(tmp_path / "a.txt", b"hello"), _write_file(tmp_path / "b.txt", b"world!")]
    result = upload.run(
        client, "b", items, on_progress=lambda item: None, cancel_event=threading.Event()
    )
    assert result.succeeded == 2
    assert result.skipped == 0
    assert result.failed == []
    assert result.total_bytes == 11
    assert client.buckets["b"]["a.txt"] == 5
    assert client.buckets["b"]["b.txt"] == 6


def test_run_skips_items_already_marked_skipped(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    item = _write_file(tmp_path / "a.txt", b"hello")
    item.status = "skipped"
    result = upload.run(
        client, "b", [item], on_progress=lambda item: None, cancel_event=threading.Event()
    )
    assert result.skipped == 1
    assert result.succeeded == 0
    assert "a.txt" not in client.buckets["b"]


def test_run_records_failures_without_aborting_the_batch(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    client.fail_keys.add("bad.txt")
    items = [
        _write_file(tmp_path / "bad.txt", b"x"),
        _write_file(tmp_path / "good.txt", b"ok"),
    ]
    result = upload.run(
        client, "b", items, on_progress=lambda item: None, cancel_event=threading.Event()
    )
    assert result.succeeded == 1
    assert len(result.failed) == 1
    assert result.failed[0].key == "bad.txt"
    assert result.failed[0].error is not None
    assert "good.txt" in client.buckets["b"]


def test_run_stops_scheduling_new_items_once_cancelled(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    cancel_event = threading.Event()
    cancel_event.set()  # cancel before starting -- nothing new should be scheduled
    items = [_write_file(tmp_path / "a.txt", b"hello")]
    result = upload.run(
        client, "b", items, on_progress=lambda item: None, cancel_event=cancel_event, max_workers=1
    )
    assert result.succeeded == 0
    assert result.failed == []
    assert "a.txt" not in client.buckets["b"]


def test_run_reports_progress_events_per_item(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    seen_statuses: list[str] = []
    item = _write_file(tmp_path / "a.txt", b"hello")
    upload.run(
        client,
        "b",
        [item],
        on_progress=lambda i: seen_statuses.append(i.status),
        cancel_event=threading.Event(),
    )
    assert "uploading" in seen_statuses
    assert seen_statuses[-1] == "done"


def test_run_records_os_error_failures_instead_of_dropping_the_item(tmp_path: Path):
    # A plain OSError (e.g. the local file vanished between scan and
    # upload) is not a ClientError -- it must still land on the item as
    # "failed" and be counted, not silently swallowed by upload.run()'s
    # concurrent.futures.wait() loop, which never calls future.result().
    client = FakeS3Client()
    client.buckets["b"] = {}
    item = _write_file(tmp_path / "bad.txt", b"x")
    client.fail_exceptions["bad.txt"] = OSError("file vanished")
    result = upload.run(
        client, "b", [item], on_progress=lambda item: None, cancel_event=threading.Event()
    )
    assert result.succeeded == 0
    assert len(result.failed) == 1
    assert result.failed[0].key == "bad.txt"
    assert result.failed[0].status == "failed"
    assert "file vanished" in result.failed[0].error
    assert "bad.txt" not in client.buckets["b"]


def test_run_records_botocore_error_failures_instead_of_dropping_the_item(tmp_path: Path):
    # botocore.exceptions.EndpointConnectionError (and friends like
    # ConnectTimeoutError/ReadTimeoutError) derive from BotoCoreError, not
    # ClientError -- same requirement as the OSError case above.
    client = FakeS3Client()
    client.buckets["b"] = {}
    item = _write_file(tmp_path / "bad.txt", b"x")
    client.fail_exceptions["bad.txt"] = EndpointConnectionError(endpoint_url="https://example.com")
    result = upload.run(
        client, "b", [item], on_progress=lambda item: None, cancel_event=threading.Event()
    )
    assert result.succeeded == 0
    assert len(result.failed) == 1
    assert result.failed[0].key == "bad.txt"
    assert result.failed[0].status == "failed"
    assert result.failed[0].error is not None
    assert "bad.txt" not in client.buckets["b"]
