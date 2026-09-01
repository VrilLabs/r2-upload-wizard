# src/r2_upload_wizard/screens/bucket_select.py
from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from r2_upload_wizard import r2_client
from r2_upload_wizard.models import BucketInfo


class BucketSelectScreen(Screen[None]):
    """Step 2: pick, create, or delete an R2 bucket."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("r", "reload", "Retry"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Loading buckets...", id="status")
        yield ListView(id="buckets")
        yield Footer()

    def on_mount(self) -> None:
        self._load_buckets()

    @work(thread=True)
    def _load_buckets(self) -> None:
        try:
            buckets = r2_client.list_buckets(self.app.state.client)
        except Exception as exc:  # noqa: BLE001 -- surfaced to the user below
            self.app.call_from_thread(self._show_error, str(exc))
            return
        self.app.call_from_thread(self._show_buckets, buckets)

    def _show_buckets(self, buckets: list[BucketInfo]) -> None:
        list_view = self.query_one("#buckets", ListView)
        list_view.remove_children()
        for bucket in buckets:
            list_view.append(ListItem(Static(bucket.name), name=bucket.name))
        self.query_one("#status", Static).update(
            f"{len(buckets)} bucket(s) -- Enter to select, n=new, d=delete, r=reload"
        )

    def _show_error(self, message: str) -> None:
        self.query_one("#status", Static).update(f"Could not list buckets: {message} (r=retry)")

    @on(ListView.Selected, "#buckets")
    def _on_bucket_selected(self, event: ListView.Selected) -> None:
        self.app.state.bucket = event.item.name
        self._advance()

    def _advance(self) -> None:
        from r2_upload_wizard.screens.source_select import SourceSelectScreen

        self.app.push_screen(SourceSelectScreen())

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_reload(self) -> None:
        self.query_one("#status", Static).update("Loading buckets...")
        self._load_buckets()
