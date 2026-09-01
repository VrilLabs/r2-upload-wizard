# tests/test_keys.py
from r2_upload_wizard.keys import build_key, normalize_prefix


def test_normalize_prefix_empty():
    assert normalize_prefix("") == ""
    assert normalize_prefix("   ") == ""


def test_normalize_prefix_strips_slashes_and_whitespace():
    assert normalize_prefix("/backups/") == "backups/"
    assert normalize_prefix(" backups ") == "backups/"


def test_normalize_prefix_nested():
    assert normalize_prefix("a/b/c") == "a/b/c/"


def test_build_key_no_prefix():
    assert build_key("", "photo.png") == "photo.png"


def test_build_key_with_prefix():
    assert build_key("backups", "sub/photo.png") == "backups/sub/photo.png"


def test_build_key_normalizes_windows_separators():
    assert build_key("backups", "sub\\photo.png") == "backups/sub/photo.png"


def test_build_key_strips_leading_slash_on_relative_path():
    assert build_key("backups", "/photo.png") == "backups/photo.png"
