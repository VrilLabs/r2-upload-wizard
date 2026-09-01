# tests/test_screen_source_select.py
from pathlib import Path

import pytest
from textual import events

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.screens.source_select import SourceSelectScreen


class _FixedSourceScreen(SourceSelectScreen):
    def __init__(self, file_path: Path | None = None, dir_path: Path | None = None):
        super().__init__()
        self._fixed_file = file_path
        self._fixed_dir = dir_path

    async def _choose_file(self):
        return self._fixed_file

    async def _choose_directory(self):
        return self._fixed_dir

    def _advance(self) -> None:
        self.app.advanced = True


class _TestApp(R2WizardApp):
    advanced = False

    def __init__(self, screen, **kwargs):
        super().__init__(**kwargs)
        self._screen = screen

    def on_mount(self, event: events.Mount) -> None:
        event.prevent_default()
        self.push_screen(self._screen)


@pytest.mark.asyncio
async def test_picking_a_single_file_populates_one_item(tmp_path: Path):
    file_path = tmp_path / "photo.png"
    file_path.write_bytes(b"12345")
    screen = _FixedSourceScreen(file_path=file_path)
    app = _TestApp(screen, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#pick-file")
        await pilot.pause()
        assert len(app.state.items) == 1
        assert app.state.items[0].key == "photo.png"
        assert app.state.items[0].size == 5
        assert app.state.source_mode == "file"
        from textual.widgets import Button

        assert app.screen.query_one("#continue", Button).disabled is False


@pytest.mark.asyncio
async def test_picking_a_directory_scans_all_files(tmp_path: Path):
    source_dir = tmp_path / "src"
    (source_dir / "nested").mkdir(parents=True)
    (source_dir / "a.txt").write_bytes(b"aaa")
    (source_dir / "nested" / "b.txt").write_bytes(b"bb")
    screen = _FixedSourceScreen(dir_path=source_dir)
    app = _TestApp(screen, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#pick-directory")
        await pilot.pause(0.2)
        assert app.state.source_mode == "directory"
        keys = {item.key for item in app.state.items}
        assert keys == {"a.txt", "nested/b.txt"}
        from textual.widgets import Button

        assert app.screen.query_one("#continue", Button).disabled is False


@pytest.mark.asyncio
async def test_continue_advances_to_destination(tmp_path: Path):
    file_path = tmp_path / "photo.png"
    file_path.write_bytes(b"12345")
    screen = _FixedSourceScreen(file_path=file_path)
    app = _TestApp(screen, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#pick-file")
        await pilot.pause()
        await pilot.click("#continue")
        await pilot.pause()
        assert app.advanced is True


@pytest.mark.asyncio
async def test_cancelling_the_picker_leaves_continue_disabled(tmp_path: Path):
    screen = _FixedSourceScreen(file_path=None)
    app = _TestApp(screen, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#pick-file")
        await pilot.pause()
        from textual.widgets import Button

        assert app.screen.query_one("#continue", Button).disabled is True
        assert app.state.items == []
