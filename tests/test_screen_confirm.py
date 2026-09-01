# tests/test_screen_confirm.py
from pathlib import Path

import pytest
from textual import events

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.models import UploadItem
from r2_upload_wizard.screens.confirm import ConfirmScreen
from tests.fakes import FakeS3Client


class _RecordingConfirmScreen(ConfirmScreen):
    def _advance(self) -> None:
        self.app.advanced = True


class _TestApp(R2WizardApp):
    advanced = False

    def __init__(self, client, mode, items, **kwargs):
        super().__init__(**kwargs)
        self.state.client = client
        self.state.bucket = "b"
        self.state.source_path = Path("/tmp/src")
        self.state.source_mode = mode
        self.state.items = items
        self.state.prefix = "prefix"

    def on_mount(self, event: events.Mount) -> None:
        event.prevent_default()
        self.push_screen(_RecordingConfirmScreen())


def _item(key: str, size: int) -> UploadItem:
    return UploadItem(local_path=Path(key), relative_path=key, key=key, size=size)


@pytest.mark.asyncio
async def test_single_file_mode_skips_existing_check(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    app = _TestApp(client, "file", [_item("a.txt", 5)], dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.query_one("#existing-choice").display is False


@pytest.mark.asyncio
async def test_directory_mode_detects_existing_files(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {"a.txt": 5}
    items = [_item("a.txt", 5), _item("b.txt", 9)]
    app = _TestApp(client, "directory", items, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        from textual.widgets import Static

        status = str(app.screen.query_one("#existing-status", Static).render())
        assert "1 of 2" in status
        assert app.screen.query_one("#existing-choice").display is True


@pytest.mark.asyncio
async def test_confirm_default_skips_matching_existing_items(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {"a.txt": 5}
    items = [_item("a.txt", 5), _item("b.txt", 9)]
    app = _TestApp(client, "directory", items, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await pilot.click("#confirm")
        await pilot.pause()
        assert items[0].status == "skipped"
        assert items[1].status == "pending"
        assert app.state.overwrite_existing is False
        assert app.advanced is True


@pytest.mark.asyncio
async def test_choosing_overwrite_all_reuploads_everything(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {"a.txt": 5}
    items = [_item("a.txt", 5), _item("b.txt", 9)]
    app = _TestApp(client, "directory", items, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await pilot.click("#choice-overwrite")
        await pilot.click("#confirm")
        await pilot.pause()
        assert items[0].status == "pending"
        assert app.state.overwrite_existing is True


@pytest.mark.asyncio
async def test_back_pops_screen_without_advancing(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    app = _TestApp(client, "file", [_item("a.txt", 5)], dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#back")
        await pilot.pause()
        assert app.advanced is False
