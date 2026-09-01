# tests/test_wizard_flow.py
from pathlib import Path

import pytest

from r2_upload_wizard import config
from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.screens.source_select import SourceSelectScreen
from tests.fakes import FakeS3Client


@pytest.fixture(autouse=True)
def _clear_cloudflare_env(monkeypatch):
    # This host's process environment can carry real CLOUDFLARE_* values
    # (e.g. from other tooling), which leaks into config.detect_env() since
    # process env takes precedence over the per-test dotenv file. Without
    # this, test_missing_credentials_block_continue is flaky/host-dependent:
    # it saw a real account ID here instead of "not set". Clearing them for
    # every test in this file keeps the whole flow hermetic.
    for name in config.ALL_VARS:
        monkeypatch.delenv(name, raising=False)


def _write_valid_env(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "export CLOUDFLARE_ACCOUNT_ID=" + "a" * 32,
                "export CLOUDFLARE_ACCESS_KEY_ID=key",
                "export CLOUDFLARE_SECRET_ACCESS_KEY=secret",
                "export CLOUDFLARE_S3_URL=https://a.r2.cloudflarestorage.com",
            ]
        )
        + "\n"
    )


async def _wait_for(pilot, predicate, attempts: int = 60) -> None:
    for _ in range(attempts):
        await pilot.pause(0.05)
        if predicate():
            return
    raise AssertionError("condition not met in time")


@pytest.mark.asyncio
async def test_full_happy_path_uploads_a_directory(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    _write_valid_env(env_path)

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "a.txt").write_bytes(b"hello")
    (source_dir / "b.txt").write_bytes(b"world!")

    fake_client = FakeS3Client()
    fake_client.buckets["photos"] = {}

    async def fake_choose_directory(self):
        return source_dir

    monkeypatch.setattr(SourceSelectScreen, "_choose_directory", fake_choose_directory)

    app = R2WizardApp(dotenv_path=env_path, client_factory=lambda **_: fake_client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#continue")  # Setup -> Bucket select
        await pilot.pause()

        from textual.widgets import ListView

        app.screen.query_one("#buckets", ListView).index = 0
        await pilot.press("enter")  # Bucket select -> Source select
        await pilot.pause()

        await pilot.click("#pick-directory")
        await _wait_for(pilot, lambda: len(app.state.items) == 2)
        await pilot.click("#continue")  # Source select -> Destination
        await pilot.pause()

        await pilot.click("#continue")  # Destination -> Confirm (default root prefix)
        await pilot.pause()
        await pilot.click("#confirm")  # Confirm -> Progress -> Summary
        await _wait_for(pilot, lambda: app.state.result is not None)

        assert app.state.result.succeeded == 2
        assert fake_client.buckets["photos"]["a.txt"] == 5
        assert fake_client.buckets["photos"]["b.txt"] == 6

        from textual.widgets import Static

        summary_text = str(app.screen.query_one("#summary", Static).render())
        assert "Succeeded: 2" in summary_text


@pytest.mark.asyncio
async def test_missing_credentials_block_continue(tmp_path: Path):
    app = R2WizardApp(dotenv_path=tmp_path / "nope.env", client_factory=lambda **_: FakeS3Client())
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Button, Static

        assert app.screen.query_one("#continue", Button).disabled is True
        status_text = str(app.screen.query_one("#status-CLOUDFLARE_ACCOUNT_ID", Static).render())
        assert "not set" in status_text


@pytest.mark.asyncio
async def test_bucket_list_failure_is_recoverable(tmp_path: Path):
    env_path = tmp_path / ".env"
    _write_valid_env(env_path)
    fake_client = FakeS3Client()

    def failing_list_buckets():
        raise RuntimeError("network down")

    fake_client.list_buckets = failing_list_buckets

    app = R2WizardApp(dotenv_path=env_path, client_factory=lambda **_: fake_client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#continue")
        await pilot.pause()
        from textual.widgets import Static

        status = str(app.screen.query_one("#status", Static).render())
        assert "network down" in status

        fake_client.buckets = {"photos": {}}
        fake_client.list_buckets = FakeS3Client.list_buckets.__get__(fake_client)
        await pilot.press("r")
        await pilot.pause()
        from textual.widgets import ListView

        assert len(app.screen.query_one("#buckets", ListView).children) == 1


@pytest.mark.asyncio
async def test_one_failed_file_does_not_abort_the_batch_and_can_be_retried(
    tmp_path: Path, monkeypatch
):
    env_path = tmp_path / ".env"
    _write_valid_env(env_path)

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "good.txt").write_bytes(b"ok")
    (source_dir / "bad.txt").write_bytes(b"boom")

    fake_client = FakeS3Client()
    fake_client.buckets["photos"] = {}
    fake_client.fail_keys.add("bad.txt")

    async def fake_choose_directory(self):
        return source_dir

    monkeypatch.setattr(SourceSelectScreen, "_choose_directory", fake_choose_directory)

    app = R2WizardApp(dotenv_path=env_path, client_factory=lambda **_: fake_client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#continue")
        await pilot.pause()
        from textual.widgets import ListView

        app.screen.query_one("#buckets", ListView).index = 0
        await pilot.press("enter")
        await pilot.pause()

        await pilot.click("#pick-directory")
        await _wait_for(pilot, lambda: len(app.state.items) == 2)
        await pilot.click("#continue")
        await pilot.pause()
        await pilot.click("#continue")
        await pilot.pause()
        await pilot.click("#confirm")
        await _wait_for(pilot, lambda: app.state.result is not None)

        assert app.state.result.succeeded == 1
        assert len(app.state.result.failed) == 1
        assert "good.txt" in fake_client.buckets["photos"]

        fake_client.fail_keys.discard("bad.txt")

        await pilot.click("#retry")
        # NOTE (bug fix -- see task-20-report.md): the brief's literal
        # predicate/assertion here expected `result.succeeded == 1` after
        # the retry, but upload.run() recomputes succeeded/failed over the
        # *entire* items list, not just the retried subset. After a
        # successful retry both good.txt (already done) and bad.txt (now
        # retried) are "done", so succeeded is 2 and failed is empty. The
        # original predicate would either hang (waiting for a count that
        # never occurs) or -- worse -- spuriously match the *stale*
        # pre-retry result object (which also had succeeded == 1) before
        # the retry's background thread even finishes, making the final
        # assertion a race. Waiting on `failed == 0` distinguishes the
        # stale result (failed has 1 item) from the real post-retry result.
        await _wait_for(
            pilot,
            lambda: app.state.result is not None and len(app.state.result.failed) == 0,
        )
        assert app.state.result.succeeded == 2
        assert "bad.txt" in fake_client.buckets["photos"]
