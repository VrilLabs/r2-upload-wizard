from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

from r2_upload_wizard.models import EnvVarStatus

REQUIRED_VARS: tuple[str, ...] = (
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_ACCESS_KEY_ID",
    "CLOUDFLARE_SECRET_ACCESS_KEY",
    "CLOUDFLARE_S3_URL",
)
OPTIONAL_VARS: tuple[str, ...] = ("CLOUDFLARE_API_TOKEN",)
ALL_VARS: tuple[str, ...] = REQUIRED_VARS + OPTIONAL_VARS

_ACCOUNT_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def parse_dotenv(path: Path) -> dict[str, str]:
    """Parse simple KEY=VALUE / export KEY=VALUE lines.

    Ignores blank lines and lines starting with '#'. Strips a single
    matching pair of surrounding quotes from the value.
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def validate_value(name: str, value: str) -> str | None:
    """Return a short reason `value` is invalid for `name`, or None if OK."""
    if not value:
        return "empty"
    if name == "CLOUDFLARE_ACCOUNT_ID":
        if not _ACCOUNT_ID_RE.match(value):
            return "expected a 32-character hex account ID"
    elif name == "CLOUDFLARE_S3_URL":
        parsed = urlparse(value)
        if parsed.scheme != "https":
            return "must be an https:// URL"
        if not parsed.netloc.endswith(".r2.cloudflarestorage.com"):
            return "host must end with .r2.cloudflarestorage.com"
    return None


def detect_env(
    dotenv_path: Path, environ: Mapping[str, str] | None = None
) -> dict[str, EnvVarStatus]:
    """Detect the 5 R2 env vars from process env (wins) then .env (fallback)."""
    environ = os.environ if environ is None else environ
    dotenv_values = parse_dotenv(dotenv_path)
    statuses: dict[str, EnvVarStatus] = {}
    for name in ALL_VARS:
        if environ.get(name):
            value, source = environ[name], "process_env"
        elif dotenv_values.get(name):
            value, source = dotenv_values[name], "dotenv"
        else:
            statuses[name] = EnvVarStatus(
                name=name, value=None, source="missing", valid=False, reason="not set"
            )
            continue
        reason = validate_value(name, value)
        statuses[name] = EnvVarStatus(
            name=name, value=value, source=source, valid=reason is None, reason=reason
        )
    return statuses


def persist(dotenv_path: Path, changed: dict[str, str]) -> None:
    """Round-trip-safe: rewrite matching KEY=VALUE lines in place, preserving
    comments/blank lines/order, and append any keys not already present."""
    existing_lines = dotenv_path.read_text().splitlines() if dotenv_path.exists() else []
    remaining = dict(changed)
    out_lines: list[str] = []
    for raw_line in existing_lines:
        stripped = raw_line.strip()
        body = stripped[len("export ") :] if stripped.startswith("export ") else stripped
        key = (
            body.partition("=")[0].strip() if "=" in body and not stripped.startswith("#") else None
        )
        if key in remaining:
            out_lines.append(f"export {key}={remaining.pop(key)}")
        else:
            out_lines.append(raw_line)
    if remaining:
        if not existing_lines:
            out_lines.append("# Cloudflare R2 credentials -- see README.md")
        elif out_lines and out_lines[-1] != "":
            out_lines.append("")
        for key, value in remaining.items():
            out_lines.append(f"export {key}={value}")
    dotenv_path.write_text("\n".join(out_lines) + "\n")


def apply_to_process_env(values: dict[str, str]) -> None:
    os.environ.update(values)
