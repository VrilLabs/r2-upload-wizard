# src/r2_upload_wizard/screens/setup.py
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Static

from r2_upload_wizard import config
from r2_upload_wizard.models import EnvVarStatus

_SECRET_VARS = {"CLOUDFLARE_SECRET_ACCESS_KEY", "CLOUDFLARE_API_TOKEN"}


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return f"{'*' * (len(value) - 4)}{value[-4:]}"


def _icon(status: EnvVarStatus) -> str:
    if status.source == "missing":
        return "x"
    return "OK" if status.valid else "!"


class SetupScreen(Screen[None]):
    """Step 1: show/fix Cloudflare R2 credential env vars."""

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="rows"):
            for name in config.ALL_VARS:
                yield Vertical(
                    Static(id=f"status-{name}"),
                    Input(
                        placeholder=f"Paste {name} to set/fix it",
                        password=name in _SECRET_VARS,
                        id=f"input-{name}",
                    ),
                    id=f"row-{name}",
                )
        yield Button("Continue", id="continue", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self._edits: dict[str, str] = {}
        self.app.state.env = config.detect_env(self.app.state.dotenv_path)
        for name in config.ALL_VARS:
            self._render_row(name)
        self._refresh_continue()

    def _render_row(self, name: str) -> None:
        status = self.app.state.env[name]
        detail = "not set"
        if status.value:
            detail = f"{_mask(status.value)} (from {status.source})"
            if not status.valid:
                detail += f" -- {status.reason}"
        self.query_one(f"#status-{name}", Static).update(f"[{_icon(status)}] {name}: {detail}")
        self.query_one(f"#input-{name}", Input).display = not status.valid

    def _all_required_valid(self) -> bool:
        return all(self.app.state.env[name].valid for name in config.REQUIRED_VARS)

    def _refresh_continue(self) -> None:
        self.query_one("#continue", Button).disabled = not self._all_required_valid()

    @on(Input.Changed)
    def _on_input_changed(self, event: Input.Changed) -> None:
        assert event.input.id is not None
        name = event.input.id.removeprefix("input-")
        value = event.value
        reason = config.validate_value(name, value)
        self._edits[name] = value
        self.app.state.env[name] = EnvVarStatus(
            name=name,
            value=value or None,
            source="dotenv",
            valid=bool(value) and reason is None,
            reason=reason,
        )
        self._render_row(name)
        self._refresh_continue()

    @on(Button.Pressed, "#continue")
    def _on_continue(self) -> None:
        if self._edits:
            config.persist(self.app.state.dotenv_path, self._edits)
            config.apply_to_process_env(self._edits)
        env = self.app.state.env
        self.app.state.client = self.app.client_factory(
            account_id=env["CLOUDFLARE_ACCOUNT_ID"].value,
            access_key_id=env["CLOUDFLARE_ACCESS_KEY_ID"].value,
            secret_access_key=env["CLOUDFLARE_SECRET_ACCESS_KEY"].value,
            s3_url=env["CLOUDFLARE_S3_URL"].value,
        )
        self._advance()

    def _advance(self) -> None:
        from r2_upload_wizard.screens.bucket_select import BucketSelectScreen

        self.app.push_screen(BucketSelectScreen())
