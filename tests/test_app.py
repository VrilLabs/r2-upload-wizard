# tests/test_app.py
from pathlib import Path

import pytest

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.screens.setup import SetupScreen


@pytest.mark.asyncio
async def test_app_boots_and_pushes_setup_screen(tmp_path: Path):
    app = R2WizardApp(dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.state.dotenv_path == tmp_path / ".env"
        assert isinstance(app.screen, SetupScreen)


@pytest.mark.asyncio
async def test_app_uses_injected_client_factory(tmp_path: Path):
    calls = []

    def fake_factory(**kwargs):
        calls.append(kwargs)
        return "fake-client"

    app = R2WizardApp(dotenv_path=tmp_path / ".env", client_factory=fake_factory)
    assert app.client_factory is fake_factory
