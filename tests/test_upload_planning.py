# tests/test_upload_planning.py
from pathlib import Path

from r2_upload_wizard import upload
from r2_upload_wizard.models import UploadItem


def _item(key: str, size: int) -> UploadItem:
    return UploadItem(local_path=Path(key), relative_path=key, key=key, size=size)


def test_guess_content_type_known_extension():
    assert upload.guess_content_type("photo.png") == "image/png"


def test_guess_content_type_unknown_extension():
    assert upload.guess_content_type("data.unknownext") is None


def test_plan_items_skips_matching_existing_when_not_overwriting():
    items = [_item("a.txt", 10), _item("b.txt", 20)]
    planned = upload.plan_items(items, existing={"a.txt": 10}, overwrite_existing=False)
    assert planned[0].status == "skipped"
    assert planned[1].status == "pending"


def test_plan_items_reuploads_when_size_differs():
    items = [_item("a.txt", 10)]
    planned = upload.plan_items(items, existing={"a.txt": 999}, overwrite_existing=False)
    assert planned[0].status == "pending"


def test_plan_items_overwrite_all_ignores_existing():
    items = [_item("a.txt", 10)]
    planned = upload.plan_items(items, existing={"a.txt": 10}, overwrite_existing=True)
    assert planned[0].status == "pending"


def test_transfer_config_matches_balanced_profile():
    assert upload.TRANSFER_CONFIG.multipart_chunksize == 64 * 1024 * 1024
    assert upload.TRANSFER_CONFIG.multipart_threshold == 256 * 1024 * 1024
    assert upload.TRANSFER_CONFIG.max_concurrency == 4
    assert upload.DEFAULT_MAX_WORKERS == 8
