# src/r2_upload_wizard/screens/setup.py
from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static


class SetupScreen(Screen[None]):
    def compose(self) -> ComposeResult:
        yield Static("Setup screen placeholder -- replaced in Task 11")
