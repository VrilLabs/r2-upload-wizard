import os
from pathlib import Path

from r2_upload_wizard import config


def test_parse_dotenv_handles_export_quotes_comments(tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# comment\n"
        "\n"
        "export CLOUDFLARE_ACCOUNT_ID=abc123\n"
        'CLOUDFLARE_S3_URL="https://abc.r2.cloudflarestorage.com"\n'
    )
    values = config.parse_dotenv(dotenv)
    assert values == {
        "CLOUDFLARE_ACCOUNT_ID": "abc123",
        "CLOUDFLARE_S3_URL": "https://abc.r2.cloudflarestorage.com",
    }


def test_parse_dotenv_missing_file_returns_empty(tmp_path: Path):
    assert config.parse_dotenv(tmp_path / "nope.env") == {}


def test_validate_value_empty_is_invalid():
    assert config.validate_value("CLOUDFLARE_ACCOUNT_ID", "") == "empty"


def test_validate_value_account_id_shape():
    assert config.validate_value("CLOUDFLARE_ACCOUNT_ID", "a" * 32) is None
    assert config.validate_value("CLOUDFLARE_ACCOUNT_ID", "not-hex") is not None


def test_validate_value_s3_url_shape():
    assert config.validate_value("CLOUDFLARE_S3_URL", "https://x.r2.cloudflarestorage.com") is None
    assert (
        config.validate_value("CLOUDFLARE_S3_URL", "http://x.r2.cloudflarestorage.com") is not None
    )
    assert config.validate_value("CLOUDFLARE_S3_URL", "https://example.com") is not None


def test_validate_value_no_rule_is_valid():
    assert config.validate_value("CLOUDFLARE_ACCESS_KEY_ID", "anything") is None


def test_detect_env_process_env_wins_over_dotenv(tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("export CLOUDFLARE_ACCESS_KEY_ID=from-dotenv\n")
    statuses = config.detect_env(dotenv, environ={"CLOUDFLARE_ACCESS_KEY_ID": "from-process"})
    status = statuses["CLOUDFLARE_ACCESS_KEY_ID"]
    assert status.value == "from-process"
    assert status.source == "process_env"


def test_detect_env_falls_back_to_dotenv(tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("export CLOUDFLARE_ACCESS_KEY_ID=from-dotenv\n")
    statuses = config.detect_env(dotenv, environ={})
    status = statuses["CLOUDFLARE_ACCESS_KEY_ID"]
    assert status.value == "from-dotenv"
    assert status.source == "dotenv"


def test_detect_env_missing_var(tmp_path: Path):
    statuses = config.detect_env(tmp_path / "nope.env", environ={})
    status = statuses["CLOUDFLARE_ACCOUNT_ID"]
    assert status.source == "missing"
    assert status.valid is False
    assert status.value is None


def test_detect_env_covers_all_vars(tmp_path: Path):
    statuses = config.detect_env(tmp_path / "nope.env", environ={})
    assert set(statuses) == set(config.ALL_VARS)
    assert config.ALL_VARS == config.REQUIRED_VARS + config.OPTIONAL_VARS


def test_persist_creates_file_with_header(tmp_path: Path):
    dotenv = tmp_path / ".env"
    config.persist(dotenv, {"CLOUDFLARE_ACCOUNT_ID": "abc"})
    text = dotenv.read_text()
    assert "export CLOUDFLARE_ACCOUNT_ID=abc" in text
    assert text.startswith("# Cloudflare R2 credentials")


def test_persist_updates_existing_line_in_place(tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("# header\nexport CLOUDFLARE_ACCOUNT_ID=old\nexport OTHER=keep\n")
    config.persist(dotenv, {"CLOUDFLARE_ACCOUNT_ID": "new"})
    lines = dotenv.read_text().splitlines()
    assert "export CLOUDFLARE_ACCOUNT_ID=new" in lines
    assert "export OTHER=keep" in lines
    assert "# header" in lines


def test_persist_appends_new_key_without_touching_others(tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("export CLOUDFLARE_ACCOUNT_ID=abc\n")
    config.persist(dotenv, {"CLOUDFLARE_S3_URL": "https://x.r2.cloudflarestorage.com"})
    text = dotenv.read_text()
    assert "export CLOUDFLARE_ACCOUNT_ID=abc" in text
    assert "export CLOUDFLARE_S3_URL=https://x.r2.cloudflarestorage.com" in text


def test_apply_to_process_env(monkeypatch):
    monkeypatch.delenv("R2_WIZARD_TEST_VAR", raising=False)
    config.apply_to_process_env({"R2_WIZARD_TEST_VAR": "value"})
    assert os.environ["R2_WIZARD_TEST_VAR"] == "value"
