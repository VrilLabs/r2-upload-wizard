# tests/test_screen_bucket_select.py
from pathlib import Path

import pytest
from textual import events

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.screens.bucket_select import BucketSelectScreen
from tests.fakes import FakeS3Client


class _RecordingBucketSelectScreen(BucketSelectScreen):
    def _advance(self) -> None:
        self.app.advanced = True


class _TestApp(R2WizardApp):
    advanced = False

    def __init__(self, client, **kwargs):
        super().__init__(**kwargs)
        self.state.client = client

    def on_mount(self, event: events.Mount) -> None:
        # Textual dispatches on_* handlers to every class in the MRO that
        # defines one, not just the most-derived override -- without this,
        # R2WizardApp.on_mount would *also* run and push a real SetupScreen
        # on top of ours. prevent_default() is Textual's documented way to
        # stop that superclass handler from also firing.
        event.prevent_default()
        self.push_screen(_RecordingBucketSelectScreen())


@pytest.mark.asyncio
async def test_lists_buckets_on_mount(tmp_path: Path):
    client = FakeS3Client()
    client.buckets = {"photos": {}, "backups": {}}
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import ListView

        list_view = app.screen.query_one("#buckets", ListView)
        assert len(list_view.children) == 2


@pytest.mark.asyncio
async def test_selecting_a_bucket_advances(tmp_path: Path):
    client = FakeS3Client()
    client.buckets = {"photos": {}}
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import ListView

        list_view = app.screen.query_one("#buckets", ListView)
        list_view.index = 0
        await pilot.press("enter")
        await pilot.pause()
        assert app.state.bucket == "photos"
        assert app.advanced is True


@pytest.mark.asyncio
async def test_list_failure_shows_error_and_retry_works(tmp_path: Path):
    client = FakeS3Client()

    def failing_list_buckets():
        raise RuntimeError("boom")

    client.list_buckets = failing_list_buckets
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Static

        status = app.screen.query_one("#status", Static)
        assert "boom" in str(status.render())

        client.buckets = {"photos": {}}
        client.list_buckets = FakeS3Client.list_buckets.__get__(client)
        await pilot.press("r")
        await pilot.pause()
        from textual.widgets import ListView

        assert len(app.screen.query_one("#buckets", ListView).children) == 1
