from pathlib import Path

from r2_upload_wizard.models import EnvVarStatus, UploadItem, UploadResult


def test_env_var_status_defaults():
    status = EnvVarStatus(name="X", value="v", source="dotenv", valid=True)
    assert status.reason is None


def test_upload_item_defaults():
    item = UploadItem(local_path=Path("f.txt"), relative_path="f.txt", key="f.txt", size=10)
    assert item.status == "pending"
    assert item.bytes_sent == 0
    assert item.error is None


def test_upload_result_failed_list():
    result = UploadResult(succeeded=1, skipped=0, failed=[], total_bytes=10, elapsed_seconds=0.5)
    assert result.failed == []
