from __future__ import annotations

import threading

from textual import on
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static


class SummaryScreen(Screen[None]):
    """Step 7: final results and what to do next."""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="summary")
        yield Static(id="failures")
        yield Button("Retry failed", id="retry", disabled=True)
        yield Button("Upload another", id="another")
        yield Button("Quit", id="quit")
        yield Footer()

    def on_mount(self) -> None:
        result = self.app.state.result
        assert result is not None
        lines = [
            f"Succeeded: {result.succeeded}",
            f"Skipped: {result.skipped}",
            f"Failed: {len(result.failed)}",
            f"Total bytes transferred: {result.total_bytes}",
            f"Elapsed: {result.elapsed_seconds:.1f}s",
        ]
        self.query_one("#summary", Static).update("\n".join(lines))
        if result.failed:
            failure_lines = [f"{item.key}: {item.error}" for item in result.failed]
            self.query_one("#failures", Static).update("\n".join(failure_lines))
            self.query_one("#retry", Button).disabled = False
        else:
            self.query_one("#failures", Static).update("")

    @on(Button.Pressed, "#retry")
    def _on_retry(self) -> None:
        state = self.app.state
        assert state.result is not None
        for item in state.result.failed:
            item.status = "pending"
            item.bytes_sent = 0
            item.error = None
        state.cancel_event = threading.Event()
        self._retry()

    def _retry(self) -> None:
        from r2_upload_wizard.screens.progress import ProgressScreen

        self.app.push_screen(ProgressScreen())

    @on(Button.Pressed, "#another")
    def _on_another(self) -> None:
        state = self.app.state
        state.items = []
        state.source_path = None
        state.result = None
        state.cancel_event = threading.Event()
        self._upload_another()

    def _upload_another(self) -> None:
        from r2_upload_wizard.screens.source_select import SourceSelectScreen

        self.app.push_screen(SourceSelectScreen())

    @on(Button.Pressed, "#quit")
    def _on_quit(self) -> None:
        self.app.exit()
