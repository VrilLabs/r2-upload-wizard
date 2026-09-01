# src/r2_upload_wizard/screens/progress.py
from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, ProgressBar

from r2_upload_wizard import upload
from r2_upload_wizard.models import UploadItem, UploadResult

_DONE_STATUSES = ("done", "skipped")


class ProgressScreen(Screen[None]):
    """Step 6: run the upload with live per-file and aggregate progress."""

    def compose(self) -> ComposeResult:
        yield Header()
        yield ProgressBar(id="aggregate", total=100)
        yield DataTable(id="files")
        yield Button("Cancel", id="cancel")
        yield Footer()

    def on_mount(self) -> None:
        state = self.app.state
        total_bytes = sum(item.size for item in state.items)
        table = self.query_one("#files", DataTable)
        # Explicit keys: without them, DataTable auto-generates opaque
        # column keys that do NOT equal the label strings, so later lookups
        # like update_cell(item.key, "Status", ...) would raise
        # CellDoesNotExist. Using the label text as the key keeps lookups
        # by "Status"/"Progress" working.
        table.add_columns(("File", "File"), ("Status", "Status"), ("Progress", "Progress"))
        for item in state.items:
            table.add_row(item.key, item.status, "0%", key=item.key)
        self.query_one("#aggregate", ProgressBar).update(total=total_bytes or 1, progress=0)
        self._run_upload()

    @work(thread=True)
    def _run_upload(self) -> None:
        state = self.app.state

        def on_progress(item: UploadItem) -> None:
            self.app.call_from_thread(self._on_progress, item)

        result = upload.run(
            state.client,
            state.bucket,
            state.items,
            on_progress=on_progress,
            cancel_event=state.cancel_event,
        )
        self.app.call_from_thread(self._finish, result)

    def _on_progress(self, item: UploadItem) -> None:
        table = self.query_one("#files", DataTable)
        if item.status in _DONE_STATUSES:
            percent = "100%"
        elif item.size:
            percent = f"{int(100 * item.bytes_sent / item.size)}%"
        else:
            percent = "0%"
        table.update_cell(item.key, "Status", item.status)
        table.update_cell(item.key, "Progress", percent)
        done_bytes = sum(
            (i.size if i.status in _DONE_STATUSES else i.bytes_sent) for i in self.app.state.items
        )
        self.query_one("#aggregate", ProgressBar).update(progress=done_bytes)

    @on(Button.Pressed, "#cancel")
    def _on_cancel(self) -> None:
        self.app.state.cancel_event.set()
        cancel_button = self.query_one("#cancel", Button)
        cancel_button.label = "Cancelling..."
        cancel_button.disabled = True

    def _finish(self, result: UploadResult) -> None:
        self.app.state.result = result
        self._advance()

    def _advance(self) -> None:
        from r2_upload_wizard.screens.summary import SummaryScreen

        self.app.push_screen(SummaryScreen())
