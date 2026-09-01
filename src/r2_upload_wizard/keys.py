# src/r2_upload_wizard/keys.py
from __future__ import annotations


def normalize_prefix(prefix: str) -> str:
    """Trim whitespace/slashes and return '' or 'a/b/' (single trailing slash)."""
    trimmed = prefix.strip().strip("/")
    return f"{trimmed}/" if trimmed else ""


def build_key(prefix: str, relative_path: str) -> str:
    """Join a normalized prefix and a relative path into an R2 object key."""
    clean_relative = relative_path.replace("\\", "/").lstrip("/")
    return normalize_prefix(prefix) + clean_relative
