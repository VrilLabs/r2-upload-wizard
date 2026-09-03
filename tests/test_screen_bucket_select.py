# tests/test_screen_bucket_select.py
import inspect
from pathlib import Path

import pytest
from textual import events

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.screens.bucket_select import BucketSelectScreen
from tests.fakes import FakeS3Client


class _RecordingBucketSelectScreen(BucketSelectScreen):
    # Textual resolves a relative CSS_PATH against
    # inspect.getfile(self.__class__), i.e. the *most-derived* class's
    # module -- not the module that declared CSS_PATH. Since this test
    # double subclasses BucketSelectScreen from within this test file,
    # without the override below Textual would look for bucket_select.tcss
    # next to this test file instead of next to screens/bucket_select.py.
    # Setting _BASE_PATH explicitly restores the correct resolution root.
    _BASE_PATH = inspect.getfile(BucketSelectScreen)

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


@pytest.mark.asyncio
async def test_create_bucket_success_auto_selects_and_advances(tmp_path: Path):
    client = FakeS3Client()
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        from textual.widgets import Input

        name_input = app.screen.query_one("#new-bucket-name", Input)
        name_input.focus()
        await pilot.press(*list("new-bucket"))
        await pilot.click("#create-confirm")
        await pilot.pause()
        assert "new-bucket" in client.buckets
        assert app.state.bucket == "new-bucket"
        assert app.advanced is True


@pytest.mark.asyncio
async def test_create_bucket_taken_name_shows_friendly_error(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["taken"] = {}
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        from textual.widgets import Input, Static

        name_input = app.screen.query_one("#new-bucket-name", Input)
        name_input.focus()
        await pilot.press(*list("taken"))
        await pilot.click("#create-confirm")
        await pilot.pause()
        message = str(app.screen.query_one("#create-message", Static).render())
        assert "taken" in message.lower()
        assert app.advanced is False


@pytest.mark.asyncio
async def test_delete_empty_bucket_with_matching_confirmation_succeeds(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["old-bucket"] = {}
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import ListView

        app.screen.query_one("#buckets", ListView).index = 0
        await pilot.press("d")
        await pilot.pause()
        from textual.widgets import Input

        confirm_input = app.screen.query_one("#delete-confirm-name", Input)
        confirm_input.focus()
        await pilot.press(*list("old-bucket"))
        await pilot.click("#delete-confirm")
        await pilot.pause()
        assert "old-bucket" not in client.buckets


@pytest.mark.asyncio
async def test_delete_refuses_non_empty_bucket(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["full-bucket"] = {"a.txt": 5}
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import ListView

        app.screen.query_one("#buckets", ListView).index = 0
        await pilot.press("d")
        await pilot.pause()
        from textual.widgets import Input

        confirm_input = app.screen.query_one("#delete-confirm-name", Input)
        confirm_input.focus()
        await pilot.press(*list("full-bucket"))
        await pilot.click("#delete-confirm")
        await pilot.pause()
        assert "full-bucket" in client.buckets
        from textual.widgets import Static

        message = str(app.screen.query_one("#delete-message", Static).render())
        assert "not empty" in message.lower() or "1" in message


@pytest.mark.asyncio
async def test_delete_refuses_mismatched_typed_name(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["old-bucket"] = {}
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import ListView

        app.screen.query_one("#buckets", ListView).index = 0
        await pilot.press("d")
        await pilot.pause()
        from textual.widgets import Input

        confirm_input = app.screen.query_one("#delete-confirm-name", Input)
        confirm_input.focus()
        await pilot.press(*list("wrong-name"))
        await pilot.click("#delete-confirm")
        await pilot.pause()
        assert "old-bucket" in client.buckets


@pytest.mark.asyncio
async def test_create_bucket_generic_error_shows_friendly_message_not_a_traceback(tmp_path: Path):
    client = FakeS3Client()

    def failing_create_bucket(Bucket):  # noqa: N803 -- matches boto3's casing
        raise RuntimeError("network blip")

    client.create_bucket = failing_create_bucket
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        from textual.widgets import Input

        name_input = app.screen.query_one("#new-bucket-name", Input)
        name_input.focus()
        await pilot.press(*list("new-bucket"))
        await pilot.click("#create-confirm")
        await pilot.pause()
        from textual.widgets import Static

        message = str(app.screen.query_one("#create-message", Static).render())
        assert "network blip" in message
        assert app.advanced is False
        assert "new-bucket" not in client.buckets


@pytest.mark.asyncio
async def test_delete_bucket_generic_error_shows_friendly_message_not_a_traceback(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["old-bucket"] = {}

    def failing_delete_bucket(Bucket):  # noqa: N803 -- matches boto3's casing
        raise RuntimeError("network blip")

    client.delete_bucket = failing_delete_bucket
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import ListView

        app.screen.query_one("#buckets", ListView).index = 0
        await pilot.press("d")
        await pilot.pause()
        from textual.widgets import Input

        confirm_input = app.screen.query_one("#delete-confirm-name", Input)
        confirm_input.focus()
        await pilot.press(*list("old-bucket"))
        await pilot.click("#delete-confirm")
        await pilot.pause()
        assert "old-bucket" in client.buckets
        from textual.widgets import Static

        message = str(app.screen.query_one("#delete-message", Static).render())
        assert "network blip" in message
