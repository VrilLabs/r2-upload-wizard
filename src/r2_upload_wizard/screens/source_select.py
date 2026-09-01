# src/r2_upload_wizard/screens/source_select.py
from __future__ import annotations

from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static
from textual_fspicker import FileOpen, SelectDirectory

from r2_upload_wizard.models import UploadItem


class SourceSelectScreen(Screen[None]):
    """Step 3: pick a local file or directory to upload."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("c", "cancel_scan", "Cancel scan"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Pick a file or a directory to upload.", id="status")
        with Horizontal():
            yield Button("Pick file", id="pick-file")
            yield Button("Pick directory", id="pick-directory")
        yield Button("Continue", id="continue", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self._scan_cancelled = False

    async def _choose_file(self) -> Path | None:
        return await self.app.push_screen_wait(FileOpen())

    async def _choose_directory(self) -> Path | None:
        return await self.app.push_screen_wait(SelectDirectory())

    @on(Button.Pressed, "#pick-file")
    @work
    async def _pick_file(self) -> None:
        chosen = await self._choose_file()
        if chosen is None:
            return
        self._set_single_file(chosen)

    @on(Button.Pressed, "#pick-directory")
    @work
    async def _pick_directory(self) -> None:
        chosen = await self._choose_directory()
        if chosen is None:
            return
        self._start_directory_scan(chosen)

    def _set_single_file(self, path: Path) -> None:
        state = self.app.state
        state.source_path = path
        state.source_mode = "file"
        size = path.stat().st_size
        state.items = [
            UploadItem(local_path=path, relative_path=path.name, key=path.name, size=size)
        ]
        self.query_one("#status", Static).update(f"1 file selected: {path.name} ({size} bytes)")
        self.query_one("#continue", Button).disabled = False

    def _start_directory_scan(self, root: Path) -> None:
        state = self.app.state
        state.source_path = root
        state.source_mode = "directory"
        state.items = []
        self._scan_cancelled = False
        self.query_one("#continue", Button).disabled = True
        self.query_one("#status", Static).update("Scanning directory...")
        self._scan_directory(root)

    @work(thread=True)
    def _scan_directory(self, root: Path) -> None:
        items: list[UploadItem] = []
        for path in root.rglob("*"):
            if self._scan_cancelled:
                return
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            items.append(
                UploadItem(
                    local_path=path, relative_path=relative, key=relative, size=path.stat().st_size
                )
            )
            if len(items) % 25 == 0:
                self.app.call_from_thread(self._report_scan_progress, len(items))
        self.app.call_from_thread(self._finish_scan, items)

    def _report_scan_progress(self, count: int) -> None:
        self.query_one("#status", Static).update(f"Scanning... {count} file(s) found so far")

    def _finish_scan(self, items: list[UploadItem]) -> None:
        self.app.state.items = items
        total_bytes = sum(item.size for item in items)
        self.query_one("#status", Static).update(f"{len(items)} file(s), {total_bytes} bytes")
        self.query_one("#continue", Button).disabled = len(items) == 0

    def action_cancel_scan(self) -> None:
        self._scan_cancelled = True

    @on(Button.Pressed, "#continue")
    def _on_continue(self) -> None:
        self._advance()

    def _advance(self) -> None:
        from r2_upload_wizard.screens.destination import DestinationScreen

        self.app.push_screen(DestinationScreen())

    def action_go_back(self) -> None:
        self.app.pop_screen()
