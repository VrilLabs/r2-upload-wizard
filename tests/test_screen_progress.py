# tests/test_screen_progress.py
from pathlib import Path

import pytest
from textual import events

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.models import UploadItem
from r2_upload_wizard.screens.progress import ProgressScreen
from tests.fakes import FakeS3Client


class _RecordingProgressScreen(ProgressScreen):
    def _advance(self) -> None:
        self.app.advanced = True


class _TestApp(R2WizardApp):
    advanced = False

    def __init__(self, client, items, **kwargs):
        super().__init__(**kwargs)
        self.state.client = client
        self.state.bucket = "b"
        self.state.items = items

    def on_mount(self, event: events.Mount) -> None:
        event.prevent_default()
        self.push_screen(_RecordingProgressScreen())


def _write_item(tmp_path: Path, name: str, content: bytes) -> UploadItem:
    path = tmp_path / name
    path.write_bytes(content)
    return UploadItem(local_path=path, relative_path=name, key=name, size=len(content))


async def _wait_until_advanced(pilot, app, attempts: int = 40) -> None:
    for _ in range(attempts):
        await pilot.pause(0.05)
        if app.advanced:
            return
    raise AssertionError("upload did not finish in time")


@pytest.mark.asyncio
async def test_upload_completes_and_advances_to_summary(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    items = [_write_item(tmp_path, "a.txt", b"hello"), _write_item(tmp_path, "b.txt", b"world!")]
    app = _TestApp(client, items, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await _wait_until_advanced(pilot, app)
        assert app.state.result is not None
        assert app.state.result.succeeded == 2


@pytest.mark.asyncio
async def test_progress_table_shows_final_status(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    items = [_write_item(tmp_path, "a.txt", b"hello")]
    app = _TestApp(client, items, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await _wait_until_advanced(pilot, app)
        from textual.widgets import DataTable

        table = app.screen.query_one("#files", DataTable)
        assert table.get_cell("a.txt", "Status") == "done"


@pytest.mark.asyncio
async def test_cancel_sets_the_cancel_event(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    items = [_write_item(tmp_path, "a.txt", b"hello"), _write_item(tmp_path, "b.txt", b"world!")]
    app = _TestApp(client, items, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.click("#cancel")
        await _wait_until_advanced(pilot, app)
        assert app.state.cancel_event.is_set()
