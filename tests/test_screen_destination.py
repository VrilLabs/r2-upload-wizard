# tests/test_screen_destination.py
from pathlib import Path

import pytest
from textual import events

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.models import UploadItem
from r2_upload_wizard.screens.destination import DestinationScreen


class _RecordingDestinationScreen(DestinationScreen):
    def _advance(self) -> None:
        self.app.advanced = True


class _TestApp(R2WizardApp):
    advanced = False

    def on_mount(self, event: events.Mount) -> None:
        event.prevent_default()
        self.state.items = [
            UploadItem(local_path=Path("a.txt"), relative_path="a.txt", key="a.txt", size=1),
            UploadItem(local_path=Path("b.txt"), relative_path="b.txt", key="b.txt", size=2),
        ]
        self.push_screen(_RecordingDestinationScreen())


@pytest.mark.asyncio
async def test_default_preview_shows_root_keys(tmp_path: Path):
    app = _TestApp(dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Static

        preview = str(app.screen.query_one("#preview", Static).render())
        assert "a.txt" in preview
        assert "b.txt" in preview


@pytest.mark.asyncio
async def test_typing_prefix_updates_preview_live(tmp_path: Path):
    app = _TestApp(dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Input

        prefix_input = app.screen.query_one("#prefix", Input)
        prefix_input.focus()
        await pilot.press(*list("backups"))
        await pilot.pause()
        from textual.widgets import Static

        preview = str(app.screen.query_one("#preview", Static).render())
        assert "backups/a.txt" in preview


@pytest.mark.asyncio
async def test_continue_stores_prefix_and_advances(tmp_path: Path):
    app = _TestApp(dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Input

        prefix_input = app.screen.query_one("#prefix", Input)
        prefix_input.focus()
        await pilot.press(*list("backups"))
        await pilot.click("#continue")
        await pilot.pause()
        assert app.state.prefix == "backups"
        assert app.advanced is True


@pytest.mark.asyncio
async def test_continue_rebuilds_item_keys_with_the_final_prefix(tmp_path: Path):
    # Regression test: the prefix typed on this screen must actually be
    # applied to every item's `key` (what upload.py sends to S3 as the
    # `Key=` argument), not just shown in the live preview text.
    app = _TestApp(dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Input

        prefix_input = app.screen.query_one("#prefix", Input)
        prefix_input.focus()
        await pilot.press(*list("backups"))
        await pilot.click("#continue")
        await pilot.pause()
        assert app.state.items[0].key == "backups/a.txt"
        assert app.state.items[1].key == "backups/b.txt"
