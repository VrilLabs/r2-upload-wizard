# src/r2_upload_wizard/app.py
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from textual.app import App

from r2_upload_wizard import r2_client
from r2_upload_wizard.models import EnvVarStatus, UploadItem, UploadResult
from r2_upload_wizard.screens.setup import SetupScreen


@dataclass
class WizardState:
    dotenv_path: Path
    env: dict[str, EnvVarStatus] = field(default_factory=dict)
    client: object | None = None
    bucket: str | None = None
    source_path: Path | None = None
    source_mode: Literal["file", "directory"] = "file"
    items: list[UploadItem] = field(default_factory=list)
    prefix: str = ""
    overwrite_existing: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)
    result: UploadResult | None = None


class R2WizardApp(App[None]):
    TITLE = "R2 Upload Wizard"

    def __init__(
        self,
        dotenv_path: Path | None = None,
        client_factory: Callable[..., object] = r2_client.build_client,
    ) -> None:
        super().__init__()
        self.client_factory = client_factory
        self.state = WizardState(dotenv_path=dotenv_path or Path(".env"))

    def on_mount(self) -> None:
        self.push_screen(SetupScreen())
