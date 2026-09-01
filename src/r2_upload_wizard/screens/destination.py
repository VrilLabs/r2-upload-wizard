# src/r2_upload_wizard/screens/destination.py
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Static

from r2_upload_wizard.keys import build_key


class DestinationScreen(Screen[None]):
    """Step 4: choose an optional destination key prefix."""

    BINDINGS = [("escape", "go_back", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Destination prefix (optional -- leave blank for bucket root):")
        yield Input(placeholder="e.g. backups/2026", id="prefix")
        yield Static(id="preview")
        yield Button("Continue", id="continue")
        yield Footer()

    def on_mount(self) -> None:
        prefix = self.app.state.prefix
        self.query_one("#prefix", Input).value = prefix
        self._update_preview(prefix)

    @on(Input.Changed, "#prefix")
    def _on_prefix_changed(self, event: Input.Changed) -> None:
        self._update_preview(event.value)

    def _update_preview(self, prefix: str) -> None:
        items = self.app.state.items
        lines = [build_key(prefix, item.relative_path) for item in items[:5]]
        text = "\n".join(lines) if lines else "(no files selected)"
        remaining = len(items) - len(lines)
        if remaining > 0:
            text += f"\n... and {remaining} more"
        self.query_one("#preview", Static).update(text)

    @on(Button.Pressed, "#continue")
    def _on_continue(self) -> None:
        self.app.state.prefix = self.query_one("#prefix", Input).value
        self._advance()

    def _advance(self) -> None:
        from r2_upload_wizard.screens.confirm import ConfirmScreen

        self.app.push_screen(ConfirmScreen())

    def action_go_back(self) -> None:
        self.app.pop_screen()
