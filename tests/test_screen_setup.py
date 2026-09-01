# tests/test_screen_setup.py
from pathlib import Path

import pytest
from textual import events

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.screens.setup import SetupScreen


class _RecordingSetupScreen(SetupScreen):
    """Test double that records advancement instead of pushing the real
    BucketSelectScreen, which doesn't exist until Task 12."""

    def _advance(self) -> None:
        self.app.advanced = True


class _TestApp(R2WizardApp):
    advanced = False

    def on_mount(self, event: events.Mount) -> None:
        # Textual dispatches on_* handlers to every class in the MRO that
        # defines one, not just the most-derived override -- without this,
        # R2WizardApp.on_mount would *also* run and push a real SetupScreen
        # on top of ours. prevent_default() is Textual's documented way to
        # stop that superclass handler from also firing.
        event.prevent_default()
        self.push_screen(_RecordingSetupScreen())


def _write_env(path: Path, **values: str) -> None:
    path.write_text("\n".join(f"export {k}={v}" for k, v in values.items()) + "\n")


@pytest.mark.asyncio
async def test_continue_disabled_until_required_vars_valid(tmp_path: Path):
    app = _TestApp(dotenv_path=tmp_path / ".env", client_factory=lambda **_: "client")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Button

        assert app.screen.query_one("#continue", Button).disabled is True


@pytest.mark.asyncio
async def test_continue_enabled_when_env_already_valid(tmp_path: Path):
    dotenv = tmp_path / ".env"
    _write_env(
        dotenv,
        CLOUDFLARE_ACCOUNT_ID="a" * 32,
        CLOUDFLARE_ACCESS_KEY_ID="key",
        CLOUDFLARE_SECRET_ACCESS_KEY="secret",
        CLOUDFLARE_S3_URL="https://a.r2.cloudflarestorage.com",
    )
    app = _TestApp(dotenv_path=dotenv, client_factory=lambda **_: "client")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Button

        assert app.screen.query_one("#continue", Button).disabled is False


@pytest.mark.asyncio
async def test_filling_in_missing_var_enables_continue_and_persists(tmp_path: Path):
    dotenv = tmp_path / ".env"
    _write_env(
        dotenv,
        CLOUDFLARE_ACCESS_KEY_ID="key",
        CLOUDFLARE_SECRET_ACCESS_KEY="secret",
        CLOUDFLARE_S3_URL="https://a.r2.cloudflarestorage.com",
    )
    app = _TestApp(dotenv_path=dotenv, client_factory=lambda **_: "client")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Button, Input

        account_input = app.screen.query_one("#input-CLOUDFLARE_ACCOUNT_ID", Input)
        account_input.focus()
        await pilot.press(*list("a" * 32))
        await pilot.pause()
        assert app.screen.query_one("#continue", Button).disabled is False
        await pilot.click("#continue")
        await pilot.pause()
        assert app.advanced is True
        assert "a" * 32 in dotenv.read_text()
        assert app.state.client == "client"


@pytest.mark.asyncio
async def test_secret_fields_are_masked_inputs(tmp_path: Path):
    app = _TestApp(dotenv_path=tmp_path / ".env", client_factory=lambda **_: "client")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Input

        secret_input = app.screen.query_one("#input-CLOUDFLARE_SECRET_ACCESS_KEY", Input)
        assert secret_input.password is True
