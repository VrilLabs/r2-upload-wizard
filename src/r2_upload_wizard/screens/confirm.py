# src/r2_upload_wizard/screens/confirm.py
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from r2_upload_wizard import r2_client
from r2_upload_wizard.upload import plan_items

_EXISTENCE_CHECK_CONCURRENCY = 16


class ConfirmScreen(Screen[None]):
    """Step 5: preview the exact action and confirm before uploading."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("y", "confirm", "Confirm"),
        ("n", "go_back", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="summary")
        yield Static("", id="existing-status")
        with Horizontal(id="existing-choice"):
            yield Button("Skip existing (default)", id="choice-skip")
            yield Button("Overwrite all", id="choice-overwrite")
        yield Static("Selected: Skip existing", id="choice-label")
        with Horizontal():
            yield Button("Confirm (y)", id="confirm")
            yield Button("Back (n)", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self._existing: dict[str, int] = {}
        self._overwrite_choice = False
        self.query_one("#existing-choice").display = False
        self._render_summary()
        state = self.app.state
        if state.source_mode == "directory" and state.items:
            self.query_one("#existing-status", Static).update("Checking for existing files...")
            self._check_existing()

    def _render_summary(self) -> None:
        state = self.app.state
        total_bytes = sum(item.size for item in state.items)
        lines = [
            f"Source: {state.source_path} ({state.source_mode})",
            f"Files: {len(state.items)}, {total_bytes} bytes",
            f"Destination: {state.bucket}/{state.prefix or '(root)'}",
        ]
        self.query_one("#summary", Static).update("\n".join(lines))

    @work(thread=True)
    def _check_existing(self) -> None:
        state = self.app.state
        existing: dict[str, int] = {}
        with ThreadPoolExecutor(max_workers=_EXISTENCE_CHECK_CONCURRENCY) as pool:
            futures = {
                pool.submit(r2_client.head_object_size, state.client, state.bucket, item.key): item
                for item in state.items
            }
            for future in futures:
                size = future.result()
                item = futures[future]
                if size is not None:
                    existing[item.key] = size
        self.app.call_from_thread(self._finish_existing_check, existing)

    def _finish_existing_check(self, existing: dict[str, int]) -> None:
        self._existing = existing
        count = sum(1 for item in self.app.state.items if existing.get(item.key) == item.size)
        status = self.query_one("#existing-status", Static)
        choice_row = self.query_one("#existing-choice")
        if count == 0:
            status.update("No destination keys already exist.")
            choice_row.display = False
        else:
            status.update(f"{count} of {len(self.app.state.items)} destination keys already exist.")
            choice_row.display = True

    @on(Button.Pressed, "#choice-skip")
    def _choose_skip(self) -> None:
        self._overwrite_choice = False
        self.query_one("#choice-label", Static).update("Selected: Skip existing")

    @on(Button.Pressed, "#choice-overwrite")
    def _choose_overwrite(self) -> None:
        self._overwrite_choice = True
        self.query_one("#choice-label", Static).update("Selected: Overwrite all")

    @on(Button.Pressed, "#confirm")
    def action_confirm(self) -> None:
        state = self.app.state
        state.overwrite_existing = self._overwrite_choice
        plan_items(state.items, self._existing, self._overwrite_choice)
        self._advance()

    @on(Button.Pressed, "#back")
    def action_go_back(self) -> None:
        self.app.pop_screen()

    def _advance(self) -> None:
        from r2_upload_wizard.screens.progress import ProgressScreen

        self.app.push_screen(ProgressScreen())
