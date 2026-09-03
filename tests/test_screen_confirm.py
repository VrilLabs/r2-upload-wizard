# tests/test_screen_confirm.py
from pathlib import Path
from threading import Event

import pytest
from botocore.exceptions import ClientError
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


@pytest.mark.asyncio
async def test_existing_check_failure_shows_friendly_message_and_does_not_block_the_screen(
    tmp_path: Path,
):
    # A generic ClientError from head_object (e.g. AccessDenied on a token
    # that can list/upload but not HeadObject) must not leave the screen
    # stuck on "Checking for existing files..." forever, and must not
    # surface as a raw exception.
    client = FakeS3Client()
    client.buckets["b"] = {}

    def failing_head_object(Bucket, Key):  # noqa: N803 -- matches boto3's casing
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "HeadObject")

    client.head_object = failing_head_object
    items = [_item("a.txt", 5), _item("b.txt", 9)]
    app = _TestApp(client, "directory", items, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        from textual.widgets import Static

        status = str(app.screen.query_one("#existing-status", Static).render())
        assert "Checking" not in status
        assert "AccessDenied" in status or "denied" in status
        assert app.screen.query_one("#existing-choice").display is False

        # The screen must still be usable after the failed check.
        await pilot.click("#confirm")
        await pilot.pause()
        assert app.advanced is True


@pytest.mark.asyncio
async def test_skip_check_binding_unblocks_the_screen_while_check_is_in_flight(tmp_path: Path):
    # Use an Event (not a sleep) so the background check is *guaranteed*
    # to still be in flight when we press "s" -- no race against a timing
    # window, so this can't be flaky under load.
    release_check = Event()
    client = FakeS3Client()
    client.buckets["b"] = {"a.txt": 5}
    real_head_object = client.head_object

    def blocking_head_object(Bucket, Key):  # noqa: N803 -- matches boto3's casing
        release_check.wait(timeout=5)
        return real_head_object(Bucket=Bucket, Key=Key)

    client.head_object = blocking_head_object
    items = [_item("a.txt", 5), _item("b.txt", 9)]
    app = _TestApp(client, "directory", items, dotenv_path=tmp_path / ".env")
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            from textual.widgets import Static

            status = str(app.screen.query_one("#existing-status", Static).render())
            assert "Checking" in status  # guaranteed still in flight, blocked on the Event

            await pilot.press("s")
            await pilot.pause()
            status = str(app.screen.query_one("#existing-status", Static).render())
            assert "Checking" not in status
            assert app.screen.query_one("#existing-choice").display is False

            await pilot.click("#confirm")
            await pilot.pause()
            assert app.advanced is True
    finally:
        # Release the blocked background thread so it can finish and the
        # test doesn't leave a dangling worker behind.
        release_check.set()
