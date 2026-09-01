from pathlib import Path

import pytest
from textual import events

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.models import UploadItem, UploadResult
from r2_upload_wizard.screens.summary import SummaryScreen


class _RecordingSummaryScreen(SummaryScreen):
    def _retry(self) -> None:
        self.app.retried = True

    def _upload_another(self) -> None:
        self.app.restarted = True


class _TestApp(R2WizardApp):
    retried = False
    restarted = False

    def __init__(self, result, **kwargs):
        super().__init__(**kwargs)
        self.state.result = result

    def on_mount(self, event: events.Mount) -> None:
        event.prevent_default()
        self.push_screen(_RecordingSummaryScreen())


def _item(key: str, size: int, status: str = "failed", error: str | None = None) -> UploadItem:
    item = UploadItem(local_path=Path(key), relative_path=key, key=key, size=size)
    item.status = status
    item.error = error
    return item


@pytest.mark.asyncio
async def test_shows_counts_and_disables_retry_when_nothing_failed(tmp_path: Path):
    result = UploadResult(succeeded=2, skipped=1, failed=[], total_bytes=30, elapsed_seconds=1.2)
    app = _TestApp(result, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Button, Static

        summary = str(app.screen.query_one("#summary", Static).render())
        assert "Succeeded: 2" in summary
        assert app.screen.query_one("#retry", Button).disabled is True


@pytest.mark.asyncio
async def test_shows_failures_and_enables_retry(tmp_path: Path):
    failed_item = _item("bad.txt", 5, error="boom")
    result = UploadResult(
        succeeded=1, skipped=0, failed=[failed_item], total_bytes=5, elapsed_seconds=0.5
    )
    app = _TestApp(result, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Button, Static

        failures = str(app.screen.query_one("#failures", Static).render())
        assert "bad.txt" in failures
        assert "boom" in failures
        assert app.screen.query_one("#retry", Button).disabled is False


@pytest.mark.asyncio
async def test_retry_resets_failed_items_and_advances(tmp_path: Path):
    failed_item = _item("bad.txt", 5, error="boom")
    result = UploadResult(
        succeeded=1, skipped=0, failed=[failed_item], total_bytes=5, elapsed_seconds=0.5
    )
    app = _TestApp(result, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#retry")
        await pilot.pause()
        assert failed_item.status == "pending"
        assert failed_item.error is None
        assert app.retried is True


@pytest.mark.asyncio
async def test_upload_another_resets_state_and_advances(tmp_path: Path):
    result = UploadResult(succeeded=1, skipped=0, failed=[], total_bytes=5, elapsed_seconds=0.5)
    app = _TestApp(result, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#another")
        await pilot.pause()
        assert app.state.items == []
        assert app.state.result is None
        assert app.restarted is True


@pytest.mark.asyncio
async def test_quit_button_exits_without_error(tmp_path: Path):
    result = UploadResult(succeeded=1, skipped=0, failed=[], total_bytes=5, elapsed_seconds=0.5)
    app = _TestApp(result, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#quit")
        await pilot.pause()
