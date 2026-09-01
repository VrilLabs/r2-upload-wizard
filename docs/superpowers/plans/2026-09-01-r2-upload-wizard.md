# R2 Upload Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an installable Textual TUI (`r2-wizard`) that walks a user through detecting/fixing Cloudflare R2 credentials, creating/deleting/selecting a bucket, picking a local file or directory, and uploading it to R2 with live progress and a final summary.

**Architecture:** A pure-Python, boto3-only engine (no external binaries) split into UI-free logic modules (`config.py`, `keys.py`, `r2_client.py`, `upload.py`, `models.py`) plus a stack of Textual screens (`screens/`) that only touch widgets and delegate all real work to those modules. Screens communicate via a single mutable `WizardState` object owned by the `R2WizardApp`. Background work (bucket listing, directory walking, uploads) runs in `@work(thread=True)` workers and reports back via `App.call_from_thread`.

**Tech Stack:** Python 3.11+, Textual, `textual-fspicker`, boto3/botocore, `uv` for env/build, `pytest` + `pytest-asyncio` for tests, `ruff` for lint/format.

**Spec:** `docs/superpowers/specs/2026-09-01-r2-upload-wizard-design.md`

## Global Constraints

- No external binary dependency (no rclone/wrangler shell-out) — boto3 only, per spec §3.
- Package must be installable via `uv tool install .` / `pipx install .` with a `r2-wizard` entry point, per spec §3/§5.
- boto3 client must set `Config(request_checksum_calculation="when_required", response_checksum_validation="when_required")` — boto3 ≥1.36 checksum default breaks R2, per spec §9.
- Upload defaults: `multipart_chunksize=64MiB`, `multipart_threshold=256MiB`, `max_concurrency=4` (parts within a file), file-level worker pool default `max_workers=8` — per spec §9.
- Never log or print secret values (`CLOUDFLARE_SECRET_ACCESS_KEY`, `CLOUDFLARE_ACCESS_KEY_ID`, `CLOUDFLARE_API_TOKEN`) anywhere; UI shows only masked previews (last 4 chars) — per spec §6 step 1, §12.
- `CLOUDFLARE_API_TOKEN` is tracked/validated for shape only, never called against any API in v1 — per spec §4.
- Deleting a non-empty bucket is always refused (object count shown), never auto-emptied — per spec §3/§4/§6 step 2.
- Per-file upload failures must never abort the rest of a batch — per spec §9/§10.
- No unhandled exception may surface as a raw traceback in the TUI — per spec §10.
- CI runs `uv run ruff check`, `uv run ruff format --check`, `uv run pytest` — per spec §11.

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/r2_upload_wizard/__init__.py`
- Create: `src/r2_upload_wizard/__main__.py`
- Create: `.env.example`
- Test: `tests/test_scaffolding.py`

**Interfaces:**
- Produces: an importable `r2_upload_wizard` package, a `main()` entry point in `r2_upload_wizard.__main__`, and a working `uv run` / `uv sync` toolchain that every later task depends on.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "r2-upload-wizard"
version = "0.1.0"
description = "A Textual TUI for uploading files and directories to Cloudflare R2."
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
dependencies = [
    "textual>=0.60",
    "textual-fspicker>=0.4",
    "boto3>=1.34",
]

[project.scripts]
r2-wizard = "r2_upload_wizard.__main__:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/r2_upload_wizard"]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.6",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

- [ ] **Step 2: Write the package skeleton**

`src/r2_upload_wizard/__init__.py`:

```python
"""r2-upload-wizard: a Textual TUI for uploading to Cloudflare R2."""

__version__ = "0.1.0"
```

`src/r2_upload_wizard/__main__.py`:

```python
from r2_upload_wizard.app import R2WizardApp


def main() -> None:
    R2WizardApp().run()


if __name__ == "__main__":
    main()
```

This imports `r2_upload_wizard.app`, which doesn't exist yet — that's expected, `__main__` is not imported by the scaffolding test below. `app.py` is created in Task 10.

- [ ] **Step 3: Write `.env.example`**

```bash
# Cloudflare R2 credentials -- copy to .env and fill in, or let
# `r2-wizard` prompt you for missing/invalid values on first run.
export CLOUDFLARE_ACCOUNT_ID=
export CLOUDFLARE_ACCESS_KEY_ID=
export CLOUDFLARE_SECRET_ACCESS_KEY=
export CLOUDFLARE_S3_URL=
export CLOUDFLARE_API_TOKEN=
```

- [ ] **Step 4: Write the scaffolding test**

```python
# tests/test_scaffolding.py
import r2_upload_wizard


def test_package_has_version():
    assert r2_upload_wizard.__version__ == "0.1.0"
```

- [ ] **Step 5: Sync the environment and run the test**

Run: `uv sync --all-groups && uv run pytest tests/test_scaffolding.py -v`
Expected: PASS (1 test)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/r2_upload_wizard/__init__.py src/r2_upload_wizard/__main__.py .env.example tests/test_scaffolding.py
git commit -m "chore: project scaffolding (pyproject, package skeleton)"
```

---

### Task 2: Data models

**Files:**
- Create: `src/r2_upload_wizard/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `EnvVarStatus`, `BucketInfo`, `UploadItem`, `UploadPlan`, `UploadResult` dataclasses used by every other module.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from pathlib import Path

from r2_upload_wizard.models import EnvVarStatus, UploadItem, UploadResult


def test_env_var_status_defaults():
    status = EnvVarStatus(name="X", value="v", source="dotenv", valid=True)
    assert status.reason is None


def test_upload_item_defaults():
    item = UploadItem(local_path=Path("f.txt"), relative_path="f.txt", key="f.txt", size=10)
    assert item.status == "pending"
    assert item.bytes_sent == 0
    assert item.error is None


def test_upload_result_failed_list():
    result = UploadResult(succeeded=1, skipped=0, failed=[], total_bytes=10, elapsed_seconds=0.5)
    assert result.failed == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'r2_upload_wizard.models'`

- [ ] **Step 3: Write the models**

```python
# src/r2_upload_wizard/models.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

EnvVarSource = Literal["process_env", "dotenv", "missing"]
UploadStatus = Literal["pending", "uploading", "done", "skipped", "failed"]


@dataclass
class EnvVarStatus:
    name: str
    value: str | None
    source: EnvVarSource
    valid: bool
    reason: str | None = None


@dataclass
class BucketInfo:
    name: str
    creation_date: datetime | None = None


@dataclass
class UploadItem:
    local_path: Path
    relative_path: str
    key: str
    size: int
    status: UploadStatus = "pending"
    bytes_sent: int = 0
    error: str | None = None


@dataclass
class UploadPlan:
    bucket: str
    items: list[UploadItem]
    overwrite_existing: bool


@dataclass
class UploadResult:
    succeeded: int
    skipped: int
    failed: list[UploadItem]
    total_bytes: int
    elapsed_seconds: float
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/models.py tests/test_models.py
git commit -m "feat: add data models (EnvVarStatus, BucketInfo, UploadItem, UploadPlan, UploadResult)"
```

---

### Task 3: Env var detection and validation

**Files:**
- Create: `src/r2_upload_wizard/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `EnvVarStatus` from `r2_upload_wizard.models`.
- Produces: `config.REQUIRED_VARS: tuple[str, ...]`, `config.OPTIONAL_VARS: tuple[str, ...]`, `config.ALL_VARS: tuple[str, ...]`, `config.parse_dotenv(path: Path) -> dict[str, str]`, `config.validate_value(name: str, value: str) -> str | None`, `config.detect_env(dotenv_path: Path, environ: Mapping[str, str] | None = None) -> dict[str, EnvVarStatus]`. `persist()`/`apply_to_process_env()` are added in Task 4.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
from pathlib import Path

from r2_upload_wizard import config


def test_parse_dotenv_handles_export_quotes_comments(tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# comment\n"
        "\n"
        'export CLOUDFLARE_ACCOUNT_ID=abc123\n'
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
    assert config.validate_value("CLOUDFLARE_S3_URL", "http://x.r2.cloudflarestorage.com") is not None
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'r2_upload_wizard.config'`

- [ ] **Step 3: Write `config.py` (detection/validation only)**

```python
# src/r2_upload_wizard/config.py
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/config.py tests/test_config.py
git commit -m "feat: env var detection and validation"
```

---

### Task 4: .env persistence

**Files:**
- Modify: `src/r2_upload_wizard/config.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `config.persist(dotenv_path: Path, changed: dict[str, str]) -> None`, `config.apply_to_process_env(values: dict[str, str]) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
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
```

Add `import os` to the top of `tests/test_config.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v -k persist_or_apply`
Expected: FAIL with `AttributeError: module 'r2_upload_wizard.config' has no attribute 'persist'`
(Run without `-k` if that filter matches nothing; the four new tests should fail/error.)

- [ ] **Step 3: Append `persist` and `apply_to_process_env` to `config.py`**

```python
def persist(dotenv_path: Path, changed: dict[str, str]) -> None:
    """Round-trip-safe: rewrite matching KEY=VALUE lines in place, preserving
    comments/blank lines/order, and append any keys not already present."""
    existing_lines = dotenv_path.read_text().splitlines() if dotenv_path.exists() else []
    remaining = dict(changed)
    out_lines: list[str] = []
    for raw_line in existing_lines:
        stripped = raw_line.strip()
        body = stripped[len("export ") :] if stripped.startswith("export ") else stripped
        key = body.partition("=")[0].strip() if "=" in body and not stripped.startswith("#") else None
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/config.py tests/test_config.py
git commit -m "feat: round-trip .env persistence for setup screen edits"
```

---

### Task 5: Object key generation

**Files:**
- Create: `src/r2_upload_wizard/keys.py`
- Test: `tests/test_keys.py`

**Interfaces:**
- Produces: `keys.normalize_prefix(prefix: str) -> str`, `keys.build_key(prefix: str, relative_path: str) -> str`.
- Note: `relative_path` is always the filename-or-relative-path that becomes the key suffix. In single-file mode, callers set `relative_path` to the file's own basename (e.g. `"photo.png"`), not an empty string — there is no special-cased empty-path behavior in `keys.py` itself.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_keys.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'r2_upload_wizard.keys'`

- [ ] **Step 3: Write `keys.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_keys.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/keys.py tests/test_keys.py
git commit -m "feat: object key construction from destination prefix + relative path"
```

---

### Task 6: R2 client factory, list buckets, head object

**Files:**
- Create: `src/r2_upload_wizard/r2_client.py`
- Test: `tests/test_r2_client.py`

**Interfaces:**
- Consumes: `BucketInfo` from `r2_upload_wizard.models`.
- Produces: `r2_client.build_client(account_id, access_key_id, secret_access_key, s3_url)` (kwargs, returns a boto3 S3 client), `r2_client.list_buckets(client) -> list[BucketInfo]`, `r2_client.head_object_size(client, bucket: str, key: str) -> int | None`, `r2_client.R2Error` (base exception, used again in Task 7).
- Testing approach: `botocore.stub.Stubber` wraps a real boto3 client so requests never hit the network but responses are shaped exactly like AWS/R2's.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_r2_client.py
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from r2_upload_wizard import r2_client


def _stubbed_client():
    client = boto3.client(
        "s3",
        endpoint_url="https://example.r2.cloudflarestorage.com",
        aws_access_key_id="key",
        aws_secret_access_key="secret",
        region_name="auto",
    )
    return client, Stubber(client)


def test_build_client_sets_checksum_config():
    client = r2_client.build_client(
        account_id="a" * 32,
        access_key_id="key",
        secret_access_key="secret",
        s3_url="https://a" * 4 + ".r2.cloudflarestorage.com",
    )
    checksum_context = client.meta.config.request_checksum_calculation
    assert checksum_context == "when_required"
    assert client.meta.config.response_checksum_validation == "when_required"


def test_list_buckets_maps_to_bucket_info():
    client, stubber = _stubbed_client()
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stubber.add_response(
        "list_buckets",
        {"Buckets": [{"Name": "photos", "CreationDate": created}], "Owner": {}},
    )
    with stubber:
        buckets = r2_client.list_buckets(client)
    assert buckets == [r2_client.BucketInfo(name="photos", creation_date=created)]


def test_head_object_size_found():
    client, stubber = _stubbed_client()
    stubber.add_response(
        "head_object",
        {"ContentLength": 42},
        expected_params={"Bucket": "b", "Key": "k"},
    )
    with stubber:
        assert r2_client.head_object_size(client, "b", "k") == 42


def test_head_object_size_missing_returns_none():
    client, stubber = _stubbed_client()
    stubber.add_client_error("head_object", service_error_code="404")
    with stubber:
        assert r2_client.head_object_size(client, "b", "missing-key") is None


def test_head_object_size_reraises_other_errors():
    client, stubber = _stubbed_client()
    stubber.add_client_error("head_object", service_error_code="AccessDenied")
    with stubber, __import__("pytest").raises(ClientError):
        r2_client.head_object_size(client, "b", "k")
```

Note: `r2_client.BucketInfo` in the test above is re-exported from `models` for
convenience — Step 3 imports `BucketInfo` into `r2_client.py`'s namespace, so
`r2_client.BucketInfo` and `models.BucketInfo` are the same class.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_r2_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'r2_upload_wizard.r2_client'`

- [ ] **Step 3: Write `r2_client.py` (client factory, list/head only)**

```python
# src/r2_upload_wizard/r2_client.py
from __future__ import annotations

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from r2_upload_wizard.models import BucketInfo

__all__ = [
    "BucketInfo",
    "R2Error",
    "build_client",
    "list_buckets",
    "head_object_size",
]


class R2Error(Exception):
    """Base class for R2-specific errors raised by this module."""


def build_client(account_id: str, access_key_id: str, secret_access_key: str, s3_url: str):
    """Build a boto3 S3 client pointed at an R2 account's S3-compatible endpoint.

    `account_id` isn't passed to boto3 directly (the endpoint URL already
    encodes it) but is accepted here so callers can pass the full env-var
    set uniformly; keeping the parameter also makes intent explicit at call
    sites and leaves room for future Cloudflare-API-based features.
    """
    del account_id  # not needed by boto3 itself; see docstring
    return boto3.client(
        "s3",
        endpoint_url=s3_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
        config=Config(
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def list_buckets(client) -> list[BucketInfo]:
    response = client.list_buckets()
    return [
        BucketInfo(name=bucket["Name"], creation_date=bucket.get("CreationDate"))
        for bucket in response.get("Buckets", [])
    ]


def head_object_size(client, bucket: str, key: str) -> int | None:
    """Return the object's size in bytes, or None if it doesn't exist."""
    try:
        response = client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return response["ContentLength"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_r2_client.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/r2_client.py tests/test_r2_client.py
git commit -m "feat: R2 client factory, list_buckets, head_object_size"
```

---

### Task 7: Bucket create/delete

**Files:**
- Modify: `src/r2_upload_wizard/r2_client.py`
- Modify: `tests/test_r2_client.py`

**Interfaces:**
- Consumes: `R2Error` from Task 6.
- Produces: `r2_client.validate_bucket_name(name: str) -> str | None`, `r2_client.is_bucket_empty(client, bucket: str) -> tuple[bool, str]` (bool, human-readable approximate count), `r2_client.create_bucket(client, name: str) -> None`, `r2_client.delete_bucket(client, name: str) -> None`, `r2_client.BucketAlreadyExistsError(R2Error)`, `r2_client.BucketNotEmptyError(R2Error)` (has an `.approx_count: str` attribute).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_r2_client.py`:

```python
def test_validate_bucket_name_rules():
    assert r2_client.validate_bucket_name("valid-name") is None
    assert r2_client.validate_bucket_name("ab") is not None  # too short
    assert r2_client.validate_bucket_name("Has-Upper") is not None
    assert r2_client.validate_bucket_name("-leading-hyphen") is not None
    assert r2_client.validate_bucket_name("trailing-hyphen-") is not None


def test_is_bucket_empty_true():
    client, stubber = _stubbed_client()
    stubber.add_response(
        "list_objects_v2",
        {"KeyCount": 0, "IsTruncated": False, "Contents": []},
        expected_params={"Bucket": "b", "MaxKeys": 1},
    )
    with stubber:
        empty, approx = r2_client.is_bucket_empty(client, "b")
    assert empty is True
    assert approx == "0"


def test_is_bucket_empty_false_with_exact_count():
    client, stubber = _stubbed_client()
    stubber.add_response(
        "list_objects_v2",
        {"KeyCount": 1, "IsTruncated": False, "Contents": [{"Key": "x"}]},
        expected_params={"Bucket": "b", "MaxKeys": 1},
    )
    with stubber:
        empty, approx = r2_client.is_bucket_empty(client, "b")
    assert empty is False
    assert approx == "1"


def test_is_bucket_empty_false_truncated_shows_plus():
    client, stubber = _stubbed_client()
    stubber.add_response(
        "list_objects_v2",
        {"KeyCount": 1, "IsTruncated": True, "Contents": [{"Key": "x"}]},
        expected_params={"Bucket": "b", "MaxKeys": 1},
    )
    with stubber:
        empty, approx = r2_client.is_bucket_empty(client, "b")
    assert empty is False
    assert approx == "1+"


def test_create_bucket_rejects_invalid_name_without_a_call():
    client, stubber = _stubbed_client()
    with stubber, __import__("pytest").raises(ValueError):
        r2_client.create_bucket(client, "AB")


def test_create_bucket_success():
    client, stubber = _stubbed_client()
    stubber.add_response("create_bucket", {}, expected_params={"Bucket": "new-bucket"})
    with stubber:
        r2_client.create_bucket(client, "new-bucket")


def test_create_bucket_already_exists_maps_to_typed_error():
    client, stubber = _stubbed_client()
    stubber.add_client_error("create_bucket", service_error_code="BucketAlreadyExists")
    with stubber, __import__("pytest").raises(r2_client.BucketAlreadyExistsError):
        r2_client.create_bucket(client, "taken")


def test_delete_bucket_refuses_when_not_empty():
    client, stubber = _stubbed_client()
    stubber.add_response(
        "list_objects_v2",
        {"KeyCount": 1, "IsTruncated": False, "Contents": [{"Key": "x"}]},
        expected_params={"Bucket": "b", "MaxKeys": 1},
    )
    with stubber, __import__("pytest").raises(r2_client.BucketNotEmptyError) as excinfo:
        r2_client.delete_bucket(client, "b")
    assert excinfo.value.approx_count == "1"


def test_delete_bucket_succeeds_when_empty():
    client, stubber = _stubbed_client()
    stubber.add_response(
        "list_objects_v2",
        {"KeyCount": 0, "IsTruncated": False, "Contents": []},
        expected_params={"Bucket": "b", "MaxKeys": 1},
    )
    stubber.add_response("delete_bucket", {}, expected_params={"Bucket": "b"})
    with stubber:
        r2_client.delete_bucket(client, "b")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_r2_client.py -v -k "bucket_name or is_bucket_empty or create_bucket or delete_bucket"`
Expected: FAIL with `AttributeError: module 'r2_upload_wizard.r2_client' has no attribute 'validate_bucket_name'`

- [ ] **Step 3: Append bucket create/delete to `r2_client.py`**

```python
import re

_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?$")


class BucketAlreadyExistsError(R2Error):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"bucket name '{name}' is already taken")


class BucketNotEmptyError(R2Error):
    def __init__(self, approx_count: str):
        self.approx_count = approx_count
        super().__init__(f"bucket is not empty ({approx_count} object(s))")


def validate_bucket_name(name: str) -> str | None:
    if not (3 <= len(name) <= 63):
        return "must be 3-63 characters"
    if not _NAME_RE.match(name):
        return "lowercase letters, digits, hyphens only; must start/end alphanumeric"
    return None


def is_bucket_empty(client, bucket: str) -> tuple[bool, str]:
    response = client.list_objects_v2(Bucket=bucket, MaxKeys=1)
    count = response.get("KeyCount", len(response.get("Contents", [])))
    if count == 0:
        return True, "0"
    approx = "1+" if response.get("IsTruncated") else str(count)
    return False, approx


def create_bucket(client, name: str) -> None:
    reason = validate_bucket_name(name)
    if reason:
        raise ValueError(reason)
    try:
        client.create_bucket(Bucket=name)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
            raise BucketAlreadyExistsError(name) from exc
        raise


def delete_bucket(client, name: str) -> None:
    empty, approx_count = is_bucket_empty(client, name)
    if not empty:
        raise BucketNotEmptyError(approx_count)
    client.delete_bucket(Bucket=name)
```

Move the `import re` line to the top of the file next to the other imports
rather than leaving it inline; add `validate_bucket_name`,
`BucketAlreadyExistsError`, `BucketNotEmptyError`, `is_bucket_empty`,
`create_bucket`, `delete_bucket` to the `__all__` list from Task 6.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_r2_client.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/r2_client.py tests/test_r2_client.py
git commit -m "feat: bucket create/delete with name validation and non-empty refusal"
```

---

### Task 8: Upload planning logic

**Files:**
- Create: `src/r2_upload_wizard/upload.py`
- Test: `tests/test_upload_planning.py`

**Interfaces:**
- Consumes: `UploadItem` from `r2_upload_wizard.models`.
- Produces: `upload.TRANSFER_CONFIG` (a `boto3.s3.transfer.TransferConfig` constant), `upload.DEFAULT_MAX_WORKERS: int`, `upload.guess_content_type(filename: str) -> str | None`, `upload.plan_items(items: list[UploadItem], existing: dict[str, int], overwrite_existing: bool) -> list[UploadItem]` (mutates and returns the same list). `run()` is added in Task 9.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_upload_planning.py
from pathlib import Path

from r2_upload_wizard import upload
from r2_upload_wizard.models import UploadItem


def _item(key: str, size: int) -> UploadItem:
    return UploadItem(local_path=Path(key), relative_path=key, key=key, size=size)


def test_guess_content_type_known_extension():
    assert upload.guess_content_type("photo.png") == "image/png"


def test_guess_content_type_unknown_extension():
    assert upload.guess_content_type("data.unknownext") is None


def test_plan_items_skips_matching_existing_when_not_overwriting():
    items = [_item("a.txt", 10), _item("b.txt", 20)]
    planned = upload.plan_items(items, existing={"a.txt": 10}, overwrite_existing=False)
    assert planned[0].status == "skipped"
    assert planned[1].status == "pending"


def test_plan_items_reuploads_when_size_differs():
    items = [_item("a.txt", 10)]
    planned = upload.plan_items(items, existing={"a.txt": 999}, overwrite_existing=False)
    assert planned[0].status == "pending"


def test_plan_items_overwrite_all_ignores_existing():
    items = [_item("a.txt", 10)]
    planned = upload.plan_items(items, existing={"a.txt": 10}, overwrite_existing=True)
    assert planned[0].status == "pending"


def test_transfer_config_matches_balanced_profile():
    assert upload.TRANSFER_CONFIG.multipart_chunksize == 64 * 1024 * 1024
    assert upload.TRANSFER_CONFIG.multipart_threshold == 256 * 1024 * 1024
    assert upload.TRANSFER_CONFIG.max_concurrency == 4
    assert upload.DEFAULT_MAX_WORKERS == 8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_upload_planning.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'r2_upload_wizard.upload'`

- [ ] **Step 3: Write `upload.py` (planning only; `run()` comes in Task 9)**

```python
# src/r2_upload_wizard/upload.py
from __future__ import annotations

import mimetypes

from boto3.s3.transfer import TransferConfig

from r2_upload_wizard.models import UploadItem

TRANSFER_CONFIG = TransferConfig(
    multipart_chunksize=64 * 1024 * 1024,
    multipart_threshold=256 * 1024 * 1024,
    max_concurrency=4,
    use_threads=True,
)

DEFAULT_MAX_WORKERS = 8


def guess_content_type(filename: str) -> str | None:
    content_type, _ = mimetypes.guess_type(filename)
    return content_type


def plan_items(
    items: list[UploadItem], existing: dict[str, int], overwrite_existing: bool
) -> list[UploadItem]:
    """Mark items whose key already exists at destination with a matching
    size as 'skipped' unless overwrite_existing is True. A size mismatch is
    treated as *not* existing (the item stays 'pending' and re-uploads),
    since a same-name/different-size file is a changed file, not a dup.
    """
    if not overwrite_existing:
        for item in items:
            if existing.get(item.key) == item.size:
                item.status = "skipped"
    return items
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_upload_planning.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/upload.py tests/test_upload_planning.py
git commit -m "feat: upload planning (skip-existing decision, content-type guess, transfer config)"
```

---

### Task 9: Threaded upload engine

**Files:**
- Modify: `src/r2_upload_wizard/upload.py`
- Create: `tests/fakes.py`
- Modify: `tests/test_upload_planning.py` (or add `tests/test_upload_run.py` — see Step 1)

**Interfaces:**
- Consumes: `TRANSFER_CONFIG`, `DEFAULT_MAX_WORKERS`, `guess_content_type` from Task 8; `UploadItem`, `UploadResult` from `models`.
- Produces: `upload.run(client, bucket: str, items: list[UploadItem], on_progress: Callable[[UploadItem], None], cancel_event: threading.Event, max_workers: int = DEFAULT_MAX_WORKERS) -> UploadResult`.
- Produces (test double, reused by screen tests in later tasks): `tests/fakes.py` — `FakeS3Client`, a boto3-client-shaped stand-in implementing `list_buckets`, `head_object`, `list_objects_v2`, `create_bucket`, `delete_bucket`, `upload_file` against an in-memory dict, so no real network/credentials are needed anywhere in the test suite.

- [ ] **Step 1: Write `tests/fakes.py`**

```python
# tests/fakes.py
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from botocore.exceptions import ClientError


def _client_error(code: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class FakeS3Client:
    """An in-memory stand-in for a boto3 S3 client, shaped to match the real
    client's method signatures closely enough for r2_client.py and
    upload.py to work against it unmodified.
    """

    def __init__(self) -> None:
        self.buckets: dict[str, dict[str, int]] = {}
        self.fail_keys: set[str] = set()  # keys whose upload_file should raise

    def list_buckets(self):
        return {
            "Buckets": [
                {"Name": name, "CreationDate": datetime(2026, 1, 1, tzinfo=timezone.utc)}
                for name in self.buckets
            ]
        }

    def head_object(self, Bucket: str, Key: str):  # noqa: N803 -- matches boto3's casing
        objects = self.buckets.get(Bucket, {})
        if Key not in objects:
            raise _client_error("404", "HeadObject")
        return {"ContentLength": objects[Key]}

    def list_objects_v2(self, Bucket: str, MaxKeys: int = 1000):  # noqa: N803
        objects = self.buckets.get(Bucket, {})
        keys = list(objects)[:MaxKeys]
        return {
            "KeyCount": len(keys),
            "IsTruncated": len(objects) > MaxKeys,
            "Contents": [{"Key": key} for key in keys],
        }

    def create_bucket(self, Bucket: str):  # noqa: N803
        if Bucket in self.buckets:
            raise _client_error("BucketAlreadyExists", "CreateBucket")
        self.buckets[Bucket] = {}

    def delete_bucket(self, Bucket: str):  # noqa: N803
        del self.buckets[Bucket]

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None, Callback=None, Config=None):  # noqa: N803
        if Key in self.fail_keys:
            raise _client_error("InternalError", "PutObject")
        size = Path(Filename).stat().st_size
        if Callback is not None:
            Callback(size)  # simulate the whole file transferring in one chunk
        time.sleep(0)  # yield, matching real threaded I/O behavior in tests
        self.buckets.setdefault(Bucket, {})[Key] = size
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_upload_run.py
import threading
from pathlib import Path

from r2_upload_wizard import upload
from r2_upload_wizard.models import UploadItem
from tests.fakes import FakeS3Client


def _write_file(path: Path, content: bytes) -> UploadItem:
    path.write_bytes(content)
    return UploadItem(local_path=path, relative_path=path.name, key=path.name, size=len(content))


def test_run_uploads_all_pending_items(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    items = [_write_file(tmp_path / "a.txt", b"hello"), _write_file(tmp_path / "b.txt", b"world!")]
    result = upload.run(client, "b", items, on_progress=lambda item: None, cancel_event=threading.Event())
    assert result.succeeded == 2
    assert result.skipped == 0
    assert result.failed == []
    assert result.total_bytes == 11
    assert client.buckets["b"]["a.txt"] == 5
    assert client.buckets["b"]["b.txt"] == 6


def test_run_skips_items_already_marked_skipped(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    item = _write_file(tmp_path / "a.txt", b"hello")
    item.status = "skipped"
    result = upload.run(client, "b", [item], on_progress=lambda item: None, cancel_event=threading.Event())
    assert result.skipped == 1
    assert result.succeeded == 0
    assert "a.txt" not in client.buckets["b"]


def test_run_records_failures_without_aborting_the_batch(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    client.fail_keys.add("bad.txt")
    items = [
        _write_file(tmp_path / "bad.txt", b"x"),
        _write_file(tmp_path / "good.txt", b"ok"),
    ]
    result = upload.run(client, "b", items, on_progress=lambda item: None, cancel_event=threading.Event())
    assert result.succeeded == 1
    assert len(result.failed) == 1
    assert result.failed[0].key == "bad.txt"
    assert result.failed[0].error is not None
    assert "good.txt" in client.buckets["b"]


def test_run_stops_scheduling_new_items_once_cancelled(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    cancel_event = threading.Event()
    cancel_event.set()  # cancel before starting -- nothing new should be scheduled
    items = [_write_file(tmp_path / "a.txt", b"hello")]
    result = upload.run(client, "b", items, on_progress=lambda item: None, cancel_event=cancel_event, max_workers=1)
    assert result.succeeded == 0
    assert result.failed == []
    assert "a.txt" not in client.buckets["b"]


def test_run_reports_progress_events_per_item(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    seen_statuses: list[str] = []
    item = _write_file(tmp_path / "a.txt", b"hello")
    upload.run(
        client,
        "b",
        [item],
        on_progress=lambda i: seen_statuses.append(i.status),
        cancel_event=threading.Event(),
    )
    assert "uploading" in seen_statuses
    assert seen_statuses[-1] == "done"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_upload_run.py -v`
Expected: FAIL with `AttributeError: module 'r2_upload_wizard.upload' has no attribute 'run'`

- [ ] **Step 4: Append `run()` to `upload.py`**

```python
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable

from botocore.exceptions import ClientError

from r2_upload_wizard.models import UploadResult


def _upload_one(client, bucket: str, item, on_progress: Callable) -> None:
    def callback(bytes_transferred: int) -> None:
        item.bytes_sent += bytes_transferred
        on_progress(item)

    extra_args = {}
    content_type = guess_content_type(item.local_path.name)
    if content_type:
        extra_args["ContentType"] = content_type

    item.status = "uploading"
    on_progress(item)
    try:
        client.upload_file(
            str(item.local_path),
            bucket,
            item.key,
            ExtraArgs=extra_args or None,
            Callback=callback,
            Config=TRANSFER_CONFIG,
        )
    except ClientError as exc:
        item.status = "failed"
        item.error = str(exc)
    else:
        item.status = "done"
    on_progress(item)


def run(
    client,
    bucket: str,
    items: list,
    on_progress: Callable,
    cancel_event: threading.Event,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> UploadResult:
    start = time.monotonic()
    to_upload = [item for item in items if item.status == "pending"]
    for item in items:
        if item.status == "skipped":
            on_progress(item)

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        initial = to_upload[:max_workers] if not cancel_event.is_set() else []
        futures = {
            pool.submit(_upload_one, client, bucket, item, on_progress): item for item in initial
        }
        next_index = len(initial)
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                del futures[future]
                if not cancel_event.is_set() and next_index < len(to_upload):
                    item = to_upload[next_index]
                    next_index += 1
                    futures[pool.submit(_upload_one, client, bucket, item, on_progress)] = item

    succeeded = sum(1 for item in items if item.status == "done")
    skipped = sum(1 for item in items if item.status == "skipped")
    failed = [item for item in items if item.status == "failed"]
    total_bytes = sum(item.size for item in items if item.status == "done")
    return UploadResult(
        succeeded=succeeded,
        skipped=skipped,
        failed=failed,
        total_bytes=total_bytes,
        elapsed_seconds=time.monotonic() - start,
    )
```

Move the `import threading`, `import time`, `from concurrent.futures import ...`,
`from typing import Callable`, `from botocore.exceptions import ClientError`,
and `from r2_upload_wizard.models import UploadResult` lines up to the top of
`upload.py` alongside the Task 8 imports, rather than leaving them inline.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_upload_run.py tests/test_upload_planning.py -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Commit**

```bash
git add src/r2_upload_wizard/upload.py tests/fakes.py tests/test_upload_run.py
git commit -m "feat: threaded upload engine with bounded concurrency and cancellation"
```

---

### Task 10: App shell and WizardState

**Files:**
- Create: `src/r2_upload_wizard/app.py`
- Create: `src/r2_upload_wizard/screens/__init__.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `EnvVarStatus`, `UploadItem`, `UploadResult` from `models`; `r2_client.build_client` as the default `client_factory`.
- Produces: `WizardState` (attributes: `dotenv_path: Path`, `env: dict[str, EnvVarStatus]`, `client`, `bucket: str | None`, `source_path: Path | None`, `source_mode: Literal["file", "directory"]`, `items: list[UploadItem]`, `prefix: str`, `overwrite_existing: bool`, `cancel_event: threading.Event`, `result: UploadResult | None`); `R2WizardApp(dotenv_path: Path | None = None, client_factory: Callable = r2_client.build_client)` with a `.state: WizardState` attribute and `.client_factory` attribute, pushing a `SetupScreen` on mount. This app is a placeholder shell until Task 11 gives `SetupScreen` real content — this task uses a temporary stub `Screen` so the app is testable end to end from the start.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_app.py
from pathlib import Path

import pytest

from r2_upload_wizard.app import R2WizardApp


@pytest.mark.asyncio
async def test_app_boots_and_pushes_setup_screen(tmp_path: Path):
    app = R2WizardApp(dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.state.dotenv_path == tmp_path / ".env"
        assert len(app.screen_stack) >= 1


@pytest.mark.asyncio
async def test_app_uses_injected_client_factory(tmp_path: Path):
    calls = []

    def fake_factory(**kwargs):
        calls.append(kwargs)
        return "fake-client"

    app = R2WizardApp(dotenv_path=tmp_path / ".env", client_factory=fake_factory)
    assert app.client_factory is fake_factory
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'r2_upload_wizard.app'`

- [ ] **Step 3: Write `screens/__init__.py` and a temporary `SetupScreen` stub, then `app.py`**

`src/r2_upload_wizard/screens/__init__.py`:

```python
"""Wizard screens, one module per step. See app.py for the screen stack."""
```

Create `src/r2_upload_wizard/screens/setup.py` as a minimal stub (Task 11
replaces this file's contents entirely with the real setup screen):

```python
# src/r2_upload_wizard/screens/setup.py
from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static


class SetupScreen(Screen[None]):
    def compose(self) -> ComposeResult:
        yield Static("Setup screen placeholder -- replaced in Task 11")
```

`src/r2_upload_wizard/app.py`:

```python
# src/r2_upload_wizard/app.py
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_app.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/app.py src/r2_upload_wizard/screens/__init__.py src/r2_upload_wizard/screens/setup.py tests/test_app.py
git commit -m "feat: app shell, WizardState, injectable client factory"
```

---

### Task 11: Setup screen (env var status + inline edit + persist)

**Files:**
- Modify: `src/r2_upload_wizard/screens/setup.py` (replaces the Task 10 stub entirely)
- Test: `tests/test_screen_setup.py`

**Interfaces:**
- Consumes: `config.ALL_VARS`, `config.REQUIRED_VARS`, `config.detect_env`, `config.validate_value`, `config.persist`, `config.apply_to_process_env` from Task 3/4; `EnvVarStatus` from `models`; `app.WizardState`/`app.R2WizardApp` from Task 10.
- Produces: `SetupScreen` (a `Screen[None]`) that, on Continue, sets `self.app.state.client` via `self.app.client_factory(...)` and pushes a `BucketSelectScreen` (imported lazily inside the handler to avoid a circular import with Task 12's module, which does not exist until the next task — see Step 3 note).

Task 12 does not exist yet, so this task's `_on_continue` references
`r2_upload_wizard.screens.bucket_select.BucketSelectScreen`, which will
`ModuleNotFoundError` until Task 12 lands. That's expected and covered by
this task's tests using a **subclass override** (see Step 1) so Task 11 is
independently testable without Task 12 existing yet.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_screen_setup.py
from pathlib import Path

import pytest

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.screens.setup import SetupScreen


class _RecordingSetupScreen(SetupScreen):
    """Test double that records advancement instead of pushing the real
    BucketSelectScreen, which doesn't exist until Task 12."""

    def _advance(self) -> None:
        self.app.advanced = True


class _TestApp(R2WizardApp):
    advanced = False

    def on_mount(self) -> None:
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_screen_setup.py -v`
Expected: FAIL (the stub `SetupScreen` from Task 10 has no `#continue`/`#input-*` widgets, and no `_advance` method to override)

- [ ] **Step 3: Replace `screens/setup.py` with the real implementation**

```python
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
```

Note the `_advance()` indirection: it exists specifically so this task's
tests can override just that one method (see `_RecordingSetupScreen` in
Step 1) without needing `BucketSelectScreen` to exist yet. Task 12 does not
need to modify this method — it already does the right thing once
`bucket_select.py` exists.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_screen_setup.py tests/test_app.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/screens/setup.py tests/test_screen_setup.py
git commit -m "feat: setup screen with live env var status, inline edit, and persist"
```

---

### Task 12: Bucket select screen — list, select, retry-on-error

**Files:**
- Create: `src/r2_upload_wizard/screens/bucket_select.py`
- Test: `tests/test_screen_bucket_select.py`

**Interfaces:**
- Consumes: `r2_client.list_buckets`, `BucketInfo` from `models`; `WizardState.client`/`WizardState.bucket`; `FakeS3Client` from `tests/fakes.py` (Task 9).
- Produces: `BucketSelectScreen` (a `Screen[None]`), which on selecting a bucket sets `self.app.state.bucket` and calls `self._advance()` (same override-point pattern as Task 11, since `SourceSelectScreen` doesn't exist until Task 15).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_screen_bucket_select.py
from pathlib import Path

import pytest

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.screens.bucket_select import BucketSelectScreen
from tests.fakes import FakeS3Client


class _RecordingBucketSelectScreen(BucketSelectScreen):
    def _advance(self) -> None:
        self.app.advanced = True


class _TestApp(R2WizardApp):
    advanced = False

    def __init__(self, client, **kwargs):
        super().__init__(**kwargs)
        self.state.client = client

    def on_mount(self) -> None:
        self.push_screen(_RecordingBucketSelectScreen())


@pytest.mark.asyncio
async def test_lists_buckets_on_mount(tmp_path: Path):
    client = FakeS3Client()
    client.buckets = {"photos": {}, "backups": {}}
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import ListView

        list_view = app.screen.query_one("#buckets", ListView)
        assert len(list_view.children) == 2


@pytest.mark.asyncio
async def test_selecting_a_bucket_advances(tmp_path: Path):
    client = FakeS3Client()
    client.buckets = {"photos": {}}
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import ListView

        list_view = app.screen.query_one("#buckets", ListView)
        list_view.index = 0
        await pilot.press("enter")
        await pilot.pause()
        assert app.state.bucket == "photos"
        assert app.advanced is True


@pytest.mark.asyncio
async def test_list_failure_shows_error_and_retry_works(tmp_path: Path):
    client = FakeS3Client()

    def failing_list_buckets():
        raise RuntimeError("boom")

    client.list_buckets = failing_list_buckets
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Static

        status = app.screen.query_one("#status", Static)
        assert "boom" in str(status.render())

        client.buckets = {"photos": {}}
        client.list_buckets = FakeS3Client.list_buckets.__get__(client)
        await pilot.press("r")
        await pilot.pause()
        from textual.widgets import ListView

        assert len(app.screen.query_one("#buckets", ListView).children) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_screen_bucket_select.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'r2_upload_wizard.screens.bucket_select'`

- [ ] **Step 3: Write `screens/bucket_select.py`**

```python
# src/r2_upload_wizard/screens/bucket_select.py
from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from r2_upload_wizard import r2_client
from r2_upload_wizard.models import BucketInfo


class BucketSelectScreen(Screen[None]):
    """Step 2: pick, create, or delete an R2 bucket."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("r", "reload", "Retry"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Loading buckets...", id="status")
        yield ListView(id="buckets")
        yield Footer()

    def on_mount(self) -> None:
        self._load_buckets()

    @work(thread=True)
    def _load_buckets(self) -> None:
        try:
            buckets = r2_client.list_buckets(self.app.state.client)
        except Exception as exc:  # noqa: BLE001 -- surfaced to the user below
            self.app.call_from_thread(self._show_error, str(exc))
            return
        self.app.call_from_thread(self._show_buckets, buckets)

    def _show_buckets(self, buckets: list[BucketInfo]) -> None:
        list_view = self.query_one("#buckets", ListView)
        list_view.remove_children()
        for bucket in buckets:
            list_view.append(ListItem(Static(bucket.name), name=bucket.name))
        self.query_one("#status", Static).update(
            f"{len(buckets)} bucket(s) -- Enter to select, n=new, d=delete, r=reload"
        )

    def _show_error(self, message: str) -> None:
        self.query_one("#status", Static).update(f"Could not list buckets: {message} (r=retry)")

    @on(ListView.Selected, "#buckets")
    def _on_bucket_selected(self, event: ListView.Selected) -> None:
        self.app.state.bucket = event.item.name
        self._advance()

    def _advance(self) -> None:
        from r2_upload_wizard.screens.source_select import SourceSelectScreen

        self.app.push_screen(SourceSelectScreen())

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_reload(self) -> None:
        self.query_one("#status", Static).update("Loading buckets...")
        self._load_buckets()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_screen_bucket_select.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/screens/bucket_select.py tests/test_screen_bucket_select.py
git commit -m "feat: bucket select screen (list, select, retry-on-error)"
```

---

### Task 13: Bucket select screen — create bucket action

**Files:**
- Modify: `src/r2_upload_wizard/screens/bucket_select.py`
- Modify: `tests/test_screen_bucket_select.py`

**Interfaces:**
- Consumes: `r2_client.create_bucket`, `r2_client.validate_bucket_name`, `r2_client.BucketAlreadyExistsError` from Task 7.
- Produces: `n` binding opens an inline create form; on success the bucket list refreshes, the new bucket is auto-selected on `WizardState.bucket`, and `self._advance()` runs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_screen_bucket_select.py`:

```python
@pytest.mark.asyncio
async def test_create_bucket_success_auto_selects_and_advances(tmp_path: Path):
    client = FakeS3Client()
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        from textual.widgets import Input

        name_input = app.screen.query_one("#new-bucket-name", Input)
        name_input.focus()
        await pilot.press(*list("new-bucket"))
        await pilot.click("#create-confirm")
        await pilot.pause()
        assert "new-bucket" in client.buckets
        assert app.state.bucket == "new-bucket"
        assert app.advanced is True


@pytest.mark.asyncio
async def test_create_bucket_taken_name_shows_friendly_error(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["taken"] = {}
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        from textual.widgets import Input, Static

        name_input = app.screen.query_one("#new-bucket-name", Input)
        name_input.focus()
        await pilot.press(*list("taken"))
        await pilot.click("#create-confirm")
        await pilot.pause()
        message = str(app.screen.query_one("#create-message", Static).render())
        assert "taken" in message.lower()
        assert app.advanced is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_screen_bucket_select.py -v -k create_bucket`
Expected: FAIL — no `#new-bucket-name` / `#create-confirm` widgets exist yet.

- [ ] **Step 3: Add the create-bucket UI and handlers to `bucket_select.py`**

Add these imports:

```python
from textual.containers import Vertical
from textual.widgets import Button, Input

from r2_upload_wizard.r2_client import BucketAlreadyExistsError
```

Add to `compose()`, right after the `ListView`:

```python
        with Vertical(id="create-row", classes="hidden"):
            yield Static(id="create-message")
            yield Input(placeholder="new-bucket-name", id="new-bucket-name")
            yield Button("Create", id="create-confirm")
```

Add `("n", "show_create", "New bucket")` to `BINDINGS`.

Add the CSS to hide the row by default -- create `src/r2_upload_wizard/screens/bucket_select.tcss` next to the module:

```css
#create-row.hidden {
    display: none;
}
```

and set `CSS_PATH = "bucket_select.tcss"` as a class attribute on `BucketSelectScreen`.

Add these methods to `BucketSelectScreen`:

```python
    def action_show_create(self) -> None:
        self.query_one("#create-row").remove_class("hidden")
        self.query_one("#new-bucket-name", Input).focus()

    @on(Button.Pressed, "#create-confirm")
    def _on_create_confirm(self) -> None:
        name = self.query_one("#new-bucket-name", Input).value.strip()
        message = self.query_one("#create-message", Static)
        try:
            r2_client.create_bucket(self.app.state.client, name)
        except ValueError as exc:
            message.update(f"Invalid name: {exc}")
            return
        except BucketAlreadyExistsError as exc:
            message.update(f"'{exc.name}' is taken -- try another name")
            return
        self.app.state.bucket = name
        self._advance()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_screen_bucket_select.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/screens/bucket_select.py src/r2_upload_wizard/screens/bucket_select.tcss tests/test_screen_bucket_select.py
git commit -m "feat: create-bucket action on bucket select screen"
```

---

### Task 14: Bucket select screen — delete bucket action

**Files:**
- Modify: `src/r2_upload_wizard/screens/bucket_select.py`
- Modify: `src/r2_upload_wizard/screens/bucket_select.tcss`
- Modify: `tests/test_screen_bucket_select.py`

**Interfaces:**
- Consumes: `r2_client.delete_bucket`, `r2_client.BucketNotEmptyError` from Task 7.
- Produces: `d` binding, on the highlighted bucket, opens a type-name-to-confirm form; refuses (no call made) if the typed name doesn't match; on delete, refuses non-empty buckets with the object count and never deletes; on success the list refreshes and nothing is auto-advanced (there's no longer a bucket selected).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_screen_bucket_select.py`:

```python
@pytest.mark.asyncio
async def test_delete_empty_bucket_with_matching_confirmation_succeeds(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["old-bucket"] = {}
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import ListView

        app.screen.query_one("#buckets", ListView).index = 0
        await pilot.press("d")
        await pilot.pause()
        from textual.widgets import Input

        confirm_input = app.screen.query_one("#delete-confirm-name", Input)
        confirm_input.focus()
        await pilot.press(*list("old-bucket"))
        await pilot.click("#delete-confirm")
        await pilot.pause()
        assert "old-bucket" not in client.buckets


@pytest.mark.asyncio
async def test_delete_refuses_non_empty_bucket(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["full-bucket"] = {"a.txt": 5}
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import ListView

        app.screen.query_one("#buckets", ListView).index = 0
        await pilot.press("d")
        await pilot.pause()
        from textual.widgets import Input

        confirm_input = app.screen.query_one("#delete-confirm-name", Input)
        confirm_input.focus()
        await pilot.press(*list("full-bucket"))
        await pilot.click("#delete-confirm")
        await pilot.pause()
        assert "full-bucket" in client.buckets
        from textual.widgets import Static

        message = str(app.screen.query_one("#delete-message", Static).render())
        assert "not empty" in message.lower() or "1" in message


@pytest.mark.asyncio
async def test_delete_refuses_mismatched_typed_name(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["old-bucket"] = {}
    app = _TestApp(client, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import ListView

        app.screen.query_one("#buckets", ListView).index = 0
        await pilot.press("d")
        await pilot.pause()
        from textual.widgets import Input

        confirm_input = app.screen.query_one("#delete-confirm-name", Input)
        confirm_input.focus()
        await pilot.press(*list("wrong-name"))
        await pilot.click("#delete-confirm")
        await pilot.pause()
        assert "old-bucket" in client.buckets
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_screen_bucket_select.py -v -k delete_`
Expected: FAIL — no `#delete-confirm-name` / `#delete-confirm` widgets exist yet.

- [ ] **Step 3: Add the delete-bucket UI and handlers to `bucket_select.py`**

Add `from r2_upload_wizard.r2_client import BucketNotEmptyError` to the
existing `from r2_upload_wizard.r2_client import BucketAlreadyExistsError`
import line (combine into one `from r2_upload_wizard.r2_client import (...)`
import).

Add to `compose()`, right after the `create-row` block:

```python
        with Vertical(id="delete-row", classes="hidden"):
            yield Static(id="delete-message")
            yield Input(placeholder="type bucket name to confirm", id="delete-confirm-name")
            yield Button("Delete", id="delete-confirm")
```

Add `("d", "show_delete", "Delete bucket")` to `BINDINGS`.

Add `#delete-row.hidden { display: none; }` to `bucket_select.tcss`.

Add these methods and one field to `BucketSelectScreen` (add `self._buckets:
list[BucketInfo] = []` in `on_mount`, set it in `_show_buckets`):

```python
    def action_show_delete(self) -> None:
        list_view = self.query_one("#buckets", ListView)
        if list_view.index is None or not self._buckets:
            return
        self._delete_target = self._buckets[list_view.index].name
        self.query_one("#delete-message", Static).update(f"Deleting '{self._delete_target}'")
        self.query_one("#delete-row").remove_class("hidden")
        self.query_one("#delete-confirm-name", Input).focus()

    @on(Button.Pressed, "#delete-confirm")
    def _on_delete_confirm(self) -> None:
        typed = self.query_one("#delete-confirm-name", Input).value.strip()
        message = self.query_one("#delete-message", Static)
        if typed != self._delete_target:
            message.update("Name doesn't match -- not deleted")
            return
        try:
            r2_client.delete_bucket(self.app.state.client, self._delete_target)
        except BucketNotEmptyError as exc:
            message.update(f"Bucket is not empty ({exc.approx_count} object(s)) -- not deleted")
            return
        message.update(f"Deleted '{self._delete_target}'")
        self.query_one("#delete-row").add_class("hidden")
        self.action_reload()
```

Update `on_mount` to initialize `self._buckets: list[BucketInfo] = []` and
`self._delete_target: str | None = None` before calling `self._load_buckets()`,
and update `_show_buckets` to set `self._buckets = buckets` as its first line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_screen_bucket_select.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/screens/bucket_select.py src/r2_upload_wizard/screens/bucket_select.tcss tests/test_screen_bucket_select.py
git commit -m "feat: delete-bucket action with non-empty refusal and typed confirmation"
```

---

### Task 15: Source select screen (file/directory picker + background scan)

**Files:**
- Create: `src/r2_upload_wizard/screens/source_select.py`
- Test: `tests/test_screen_source_select.py`

**Interfaces:**
- Consumes: `UploadItem` from `models`; `textual_fspicker.FileOpen`, `textual_fspicker.SelectDirectory`.
- Produces: `SourceSelectScreen`. Sets `WizardState.source_path`, `WizardState.source_mode`, `WizardState.items`. `_choose_file()`/`_choose_directory()` are thin async wrappers around `push_screen_wait(FileOpen())`/`push_screen_wait(SelectDirectory())` specifically so tests can override them without driving a real filesystem-picker modal (same override-point pattern as `_advance()` in earlier tasks).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_screen_source_select.py
from pathlib import Path

import pytest

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.screens.source_select import SourceSelectScreen


class _FixedSourceScreen(SourceSelectScreen):
    def __init__(self, file_path: Path | None = None, dir_path: Path | None = None):
        super().__init__()
        self._fixed_file = file_path
        self._fixed_dir = dir_path

    async def _choose_file(self):
        return self._fixed_file

    async def _choose_directory(self):
        return self._fixed_dir

    def _advance(self) -> None:
        self.app.advanced = True


class _TestApp(R2WizardApp):
    advanced = False

    def __init__(self, screen, **kwargs):
        super().__init__(**kwargs)
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


@pytest.mark.asyncio
async def test_picking_a_single_file_populates_one_item(tmp_path: Path):
    file_path = tmp_path / "photo.png"
    file_path.write_bytes(b"12345")
    screen = _FixedSourceScreen(file_path=file_path)
    app = _TestApp(screen, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#pick-file")
        await pilot.pause()
        assert len(app.state.items) == 1
        assert app.state.items[0].key == "photo.png"
        assert app.state.items[0].size == 5
        assert app.state.source_mode == "file"
        from textual.widgets import Button

        assert app.screen.query_one("#continue", Button).disabled is False


@pytest.mark.asyncio
async def test_picking_a_directory_scans_all_files(tmp_path: Path):
    source_dir = tmp_path / "src"
    (source_dir / "nested").mkdir(parents=True)
    (source_dir / "a.txt").write_bytes(b"aaa")
    (source_dir / "nested" / "b.txt").write_bytes(b"bb")
    screen = _FixedSourceScreen(dir_path=source_dir)
    app = _TestApp(screen, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#pick-directory")
        await pilot.pause(0.2)
        assert app.state.source_mode == "directory"
        keys = {item.key for item in app.state.items}
        assert keys == {"a.txt", "nested/b.txt"}
        from textual.widgets import Button

        assert app.screen.query_one("#continue", Button).disabled is False


@pytest.mark.asyncio
async def test_continue_advances_to_destination(tmp_path: Path):
    file_path = tmp_path / "photo.png"
    file_path.write_bytes(b"12345")
    screen = _FixedSourceScreen(file_path=file_path)
    app = _TestApp(screen, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#pick-file")
        await pilot.pause()
        await pilot.click("#continue")
        await pilot.pause()
        assert app.advanced is True


@pytest.mark.asyncio
async def test_cancelling_the_picker_leaves_continue_disabled(tmp_path: Path):
    screen = _FixedSourceScreen(file_path=None)
    app = _TestApp(screen, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#pick-file")
        await pilot.pause()
        from textual.widgets import Button

        assert app.screen.query_one("#continue", Button).disabled is True
        assert app.state.items == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_screen_source_select.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'r2_upload_wizard.screens.source_select'`

- [ ] **Step 3: Write `screens/source_select.py`**

```python
# src/r2_upload_wizard/screens/source_select.py
from __future__ import annotations

from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static
from textual_fspicker import FileOpen, SelectDirectory

from r2_upload_wizard.models import UploadItem


class SourceSelectScreen(Screen[None]):
    """Step 3: pick a local file or directory to upload."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("c", "cancel_scan", "Cancel scan"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Pick a file or a directory to upload.", id="status")
        with Horizontal():
            yield Button("Pick file", id="pick-file")
            yield Button("Pick directory", id="pick-directory")
        yield Button("Continue", id="continue", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self._scan_cancelled = False

    async def _choose_file(self) -> Path | None:
        return await self.app.push_screen_wait(FileOpen())

    async def _choose_directory(self) -> Path | None:
        return await self.app.push_screen_wait(SelectDirectory())

    @on(Button.Pressed, "#pick-file")
    @work
    async def _pick_file(self) -> None:
        chosen = await self._choose_file()
        if chosen is None:
            return
        self._set_single_file(chosen)

    @on(Button.Pressed, "#pick-directory")
    @work
    async def _pick_directory(self) -> None:
        chosen = await self._choose_directory()
        if chosen is None:
            return
        self._start_directory_scan(chosen)

    def _set_single_file(self, path: Path) -> None:
        state = self.app.state
        state.source_path = path
        state.source_mode = "file"
        size = path.stat().st_size
        state.items = [
            UploadItem(local_path=path, relative_path=path.name, key=path.name, size=size)
        ]
        self.query_one("#status", Static).update(f"1 file selected: {path.name} ({size} bytes)")
        self.query_one("#continue", Button).disabled = False

    def _start_directory_scan(self, root: Path) -> None:
        state = self.app.state
        state.source_path = root
        state.source_mode = "directory"
        state.items = []
        self._scan_cancelled = False
        self.query_one("#continue", Button).disabled = True
        self.query_one("#status", Static).update("Scanning directory...")
        self._scan_directory(root)

    @work(thread=True)
    def _scan_directory(self, root: Path) -> None:
        items: list[UploadItem] = []
        for path in root.rglob("*"):
            if self._scan_cancelled:
                return
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            items.append(
                UploadItem(
                    local_path=path, relative_path=relative, key=relative, size=path.stat().st_size
                )
            )
            if len(items) % 25 == 0:
                self.app.call_from_thread(self._report_scan_progress, len(items))
        self.app.call_from_thread(self._finish_scan, items)

    def _report_scan_progress(self, count: int) -> None:
        self.query_one("#status", Static).update(f"Scanning... {count} file(s) found so far")

    def _finish_scan(self, items: list[UploadItem]) -> None:
        self.app.state.items = items
        total_bytes = sum(item.size for item in items)
        self.query_one("#status", Static).update(f"{len(items)} file(s), {total_bytes} bytes")
        self.query_one("#continue", Button).disabled = len(items) == 0

    def action_cancel_scan(self) -> None:
        self._scan_cancelled = True

    @on(Button.Pressed, "#continue")
    def _on_continue(self) -> None:
        self._advance()

    def _advance(self) -> None:
        from r2_upload_wizard.screens.destination import DestinationScreen

        self.app.push_screen(DestinationScreen())

    def action_go_back(self) -> None:
        self.app.pop_screen()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_screen_source_select.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/screens/source_select.py tests/test_screen_source_select.py
git commit -m "feat: source select screen (file/directory picker, background directory scan)"
```

---

### Task 16: Destination screen (prefix input + live key preview)

**Files:**
- Create: `src/r2_upload_wizard/screens/destination.py`
- Test: `tests/test_screen_destination.py`

**Interfaces:**
- Consumes: `keys.build_key` from Task 5; `WizardState.items`/`WizardState.prefix`.
- Produces: `DestinationScreen`, setting `WizardState.prefix` on Continue.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_screen_destination.py
from pathlib import Path

import pytest

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.models import UploadItem
from r2_upload_wizard.screens.destination import DestinationScreen


class _RecordingDestinationScreen(DestinationScreen):
    def _advance(self) -> None:
        self.app.advanced = True


class _TestApp(R2WizardApp):
    advanced = False

    def on_mount(self) -> None:
        self.state.items = [
            UploadItem(local_path=Path("a.txt"), relative_path="a.txt", key="a.txt", size=1),
            UploadItem(local_path=Path("b.txt"), relative_path="b.txt", key="b.txt", size=2),
        ]
        self.push_screen(_RecordingDestinationScreen())


@pytest.mark.asyncio
async def test_default_preview_shows_root_keys(tmp_path: Path):
    app = _TestApp(dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Static

        preview = str(app.screen.query_one("#preview", Static).render())
        assert "a.txt" in preview
        assert "b.txt" in preview


@pytest.mark.asyncio
async def test_typing_prefix_updates_preview_live(tmp_path: Path):
    app = _TestApp(dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Input

        prefix_input = app.screen.query_one("#prefix", Input)
        prefix_input.focus()
        await pilot.press(*list("backups"))
        await pilot.pause()
        from textual.widgets import Static

        preview = str(app.screen.query_one("#preview", Static).render())
        assert "backups/a.txt" in preview


@pytest.mark.asyncio
async def test_continue_stores_prefix_and_advances(tmp_path: Path):
    app = _TestApp(dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Input

        prefix_input = app.screen.query_one("#prefix", Input)
        prefix_input.focus()
        await pilot.press(*list("backups"))
        await pilot.click("#continue")
        await pilot.pause()
        assert app.state.prefix == "backups"
        assert app.advanced is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_screen_destination.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'r2_upload_wizard.screens.destination'`

- [ ] **Step 3: Write `screens/destination.py`**

```python
# src/r2_upload_wizard/screens/destination.py
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Static

from r2_upload_wizard.keys import build_key


class DestinationScreen(Screen[None]):
    """Step 4: choose an optional destination key prefix."""

    BINDINGS = [("escape", "go_back", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Destination prefix (optional -- leave blank for bucket root):")
        yield Input(placeholder="e.g. backups/2026", id="prefix")
        yield Static(id="preview")
        yield Button("Continue", id="continue")
        yield Footer()

    def on_mount(self) -> None:
        prefix = self.app.state.prefix
        self.query_one("#prefix", Input).value = prefix
        self._update_preview(prefix)

    @on(Input.Changed, "#prefix")
    def _on_prefix_changed(self, event: Input.Changed) -> None:
        self._update_preview(event.value)

    def _update_preview(self, prefix: str) -> None:
        items = self.app.state.items
        lines = [build_key(prefix, item.relative_path) for item in items[:5]]
        text = "\n".join(lines) if lines else "(no files selected)"
        remaining = len(items) - len(lines)
        if remaining > 0:
            text += f"\n... and {remaining} more"
        self.query_one("#preview", Static).update(text)

    @on(Button.Pressed, "#continue")
    def _on_continue(self) -> None:
        self.app.state.prefix = self.query_one("#prefix", Input).value
        self._advance()

    def _advance(self) -> None:
        from r2_upload_wizard.screens.confirm import ConfirmScreen

        self.app.push_screen(ConfirmScreen())

    def action_go_back(self) -> None:
        self.app.pop_screen()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_screen_destination.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/screens/destination.py tests/test_screen_destination.py
git commit -m "feat: destination screen with live key preview"
```

---

### Task 17: Confirm screen (preview, existence check, skip/overwrite choice)

**Files:**
- Create: `src/r2_upload_wizard/screens/confirm.py`
- Test: `tests/test_screen_confirm.py`

**Interfaces:**
- Consumes: `r2_client.head_object_size` from Task 6; `upload.plan_items` from Task 8; `FakeS3Client` from `tests/fakes.py`.
- Produces: `ConfirmScreen`, setting `WizardState.overwrite_existing` and mutating `WizardState.items[*].status` to `"skipped"` where applicable before advancing to `ProgressScreen`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_screen_confirm.py
from pathlib import Path

import pytest

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.models import UploadItem
from r2_upload_wizard.screens.confirm import ConfirmScreen
from tests.fakes import FakeS3Client


class _RecordingConfirmScreen(ConfirmScreen):
    def _advance(self) -> None:
        self.app.advanced = True


class _TestApp(R2WizardApp):
    advanced = False

    def __init__(self, client, mode, items, **kwargs):
        super().__init__(**kwargs)
        self.state.client = client
        self.state.bucket = "b"
        self.state.source_path = Path("/tmp/src")
        self.state.source_mode = mode
        self.state.items = items
        self.state.prefix = "prefix"

    def on_mount(self) -> None:
        self.push_screen(_RecordingConfirmScreen())


def _item(key: str, size: int) -> UploadItem:
    return UploadItem(local_path=Path(key), relative_path=key, key=key, size=size)


@pytest.mark.asyncio
async def test_single_file_mode_skips_existing_check(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    app = _TestApp(client, "file", [_item("a.txt", 5)], dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.query_one("#existing-choice").display is False


@pytest.mark.asyncio
async def test_directory_mode_detects_existing_files(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {"a.txt": 5}
    items = [_item("a.txt", 5), _item("b.txt", 9)]
    app = _TestApp(client, "directory", items, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        from textual.widgets import Static

        status = str(app.screen.query_one("#existing-status", Static).render())
        assert "1 of 2" in status
        assert app.screen.query_one("#existing-choice").display is True


@pytest.mark.asyncio
async def test_confirm_default_skips_matching_existing_items(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {"a.txt": 5}
    items = [_item("a.txt", 5), _item("b.txt", 9)]
    app = _TestApp(client, "directory", items, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await pilot.click("#confirm")
        await pilot.pause()
        assert items[0].status == "skipped"
        assert items[1].status == "pending"
        assert app.state.overwrite_existing is False
        assert app.advanced is True


@pytest.mark.asyncio
async def test_choosing_overwrite_all_reuploads_everything(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {"a.txt": 5}
    items = [_item("a.txt", 5), _item("b.txt", 9)]
    app = _TestApp(client, "directory", items, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await pilot.click("#choice-overwrite")
        await pilot.click("#confirm")
        await pilot.pause()
        assert items[0].status == "pending"
        assert app.state.overwrite_existing is True


@pytest.mark.asyncio
async def test_back_pops_screen_without_advancing(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    app = _TestApp(client, "file", [_item("a.txt", 5)], dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#back")
        await pilot.pause()
        assert app.advanced is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_screen_confirm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'r2_upload_wizard.screens.confirm'`

- [ ] **Step 3: Write `screens/confirm.py`**

```python
# src/r2_upload_wizard/screens/confirm.py
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from r2_upload_wizard import r2_client
from r2_upload_wizard.upload import plan_items

_EXISTENCE_CHECK_CONCURRENCY = 16


class ConfirmScreen(Screen[None]):
    """Step 5: preview the exact action and confirm before uploading."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("y", "confirm", "Confirm"),
        ("n", "go_back", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="summary")
        yield Static("", id="existing-status")
        with Horizontal(id="existing-choice"):
            yield Button("Skip existing (default)", id="choice-skip")
            yield Button("Overwrite all", id="choice-overwrite")
        yield Static("Selected: Skip existing", id="choice-label")
        with Horizontal():
            yield Button("Confirm (y)", id="confirm")
            yield Button("Back (n)", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self._existing: dict[str, int] = {}
        self._overwrite_choice = False
        self.query_one("#existing-choice").display = False
        self._render_summary()
        state = self.app.state
        if state.source_mode == "directory" and state.items:
            self.query_one("#existing-status", Static).update("Checking for existing files...")
            self._check_existing()

    def _render_summary(self) -> None:
        state = self.app.state
        total_bytes = sum(item.size for item in state.items)
        lines = [
            f"Source: {state.source_path} ({state.source_mode})",
            f"Files: {len(state.items)}, {total_bytes} bytes",
            f"Destination: {state.bucket}/{state.prefix or '(root)'}",
        ]
        self.query_one("#summary", Static).update("\n".join(lines))

    @work(thread=True)
    def _check_existing(self) -> None:
        state = self.app.state
        existing: dict[str, int] = {}
        with ThreadPoolExecutor(max_workers=_EXISTENCE_CHECK_CONCURRENCY) as pool:
            futures = {
                pool.submit(r2_client.head_object_size, state.client, state.bucket, item.key): item
                for item in state.items
            }
            for future in futures:
                size = future.result()
                item = futures[future]
                if size is not None:
                    existing[item.key] = size
        self.app.call_from_thread(self._finish_existing_check, existing)

    def _finish_existing_check(self, existing: dict[str, int]) -> None:
        self._existing = existing
        count = sum(1 for item in self.app.state.items if existing.get(item.key) == item.size)
        status = self.query_one("#existing-status", Static)
        choice_row = self.query_one("#existing-choice")
        if count == 0:
            status.update("No destination keys already exist.")
            choice_row.display = False
        else:
            status.update(
                f"{count} of {len(self.app.state.items)} destination keys already exist."
            )
            choice_row.display = True

    @on(Button.Pressed, "#choice-skip")
    def _choose_skip(self) -> None:
        self._overwrite_choice = False
        self.query_one("#choice-label", Static).update("Selected: Skip existing")

    @on(Button.Pressed, "#choice-overwrite")
    def _choose_overwrite(self) -> None:
        self._overwrite_choice = True
        self.query_one("#choice-label", Static).update("Selected: Overwrite all")

    @on(Button.Pressed, "#confirm")
    def action_confirm(self) -> None:
        state = self.app.state
        state.overwrite_existing = self._overwrite_choice
        plan_items(state.items, self._existing, self._overwrite_choice)
        self._advance()

    @on(Button.Pressed, "#back")
    def action_go_back(self) -> None:
        self.app.pop_screen()

    def _advance(self) -> None:
        from r2_upload_wizard.screens.progress import ProgressScreen

        self.app.push_screen(ProgressScreen())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_screen_confirm.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/screens/confirm.py tests/test_screen_confirm.py
git commit -m "feat: confirm screen with existing-key check and skip/overwrite choice"
```

---

### Task 18: Progress screen (live upload with cancel)

**Files:**
- Create: `src/r2_upload_wizard/screens/progress.py`
- Test: `tests/test_screen_progress.py`

**Interfaces:**
- Consumes: `upload.run` from Task 9; `WizardState.items`/`bucket`/`client`/`cancel_event`.
- Produces: `ProgressScreen`, setting `WizardState.result` before advancing to `SummaryScreen`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_screen_progress.py
from pathlib import Path

import pytest

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.models import UploadItem
from r2_upload_wizard.screens.progress import ProgressScreen
from tests.fakes import FakeS3Client


class _RecordingProgressScreen(ProgressScreen):
    def _advance(self) -> None:
        self.app.advanced = True


class _TestApp(R2WizardApp):
    advanced = False

    def __init__(self, client, items, **kwargs):
        super().__init__(**kwargs)
        self.state.client = client
        self.state.bucket = "b"
        self.state.items = items

    def on_mount(self) -> None:
        self.push_screen(_RecordingProgressScreen())


def _write_item(tmp_path: Path, name: str, content: bytes) -> UploadItem:
    path = tmp_path / name
    path.write_bytes(content)
    return UploadItem(local_path=path, relative_path=name, key=name, size=len(content))


async def _wait_until_advanced(pilot, app, attempts: int = 40) -> None:
    for _ in range(attempts):
        await pilot.pause(0.05)
        if app.advanced:
            return
    raise AssertionError("upload did not finish in time")


@pytest.mark.asyncio
async def test_upload_completes_and_advances_to_summary(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    items = [_write_item(tmp_path, "a.txt", b"hello"), _write_item(tmp_path, "b.txt", b"world!")]
    app = _TestApp(client, items, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await _wait_until_advanced(pilot, app)
        assert app.state.result is not None
        assert app.state.result.succeeded == 2


@pytest.mark.asyncio
async def test_progress_table_shows_final_status(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    items = [_write_item(tmp_path, "a.txt", b"hello")]
    app = _TestApp(client, items, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await _wait_until_advanced(pilot, app)
        from textual.widgets import DataTable

        table = app.screen.query_one("#files", DataTable)
        assert table.get_cell("a.txt", "Status") == "done"


@pytest.mark.asyncio
async def test_cancel_sets_the_cancel_event(tmp_path: Path):
    client = FakeS3Client()
    client.buckets["b"] = {}
    items = [_write_item(tmp_path, "a.txt", b"hello"), _write_item(tmp_path, "b.txt", b"world!")]
    app = _TestApp(client, items, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.click("#cancel")
        await _wait_until_advanced(pilot, app)
        assert app.state.cancel_event.is_set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_screen_progress.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'r2_upload_wizard.screens.progress'`

- [ ] **Step 3: Write `screens/progress.py`**

```python
# src/r2_upload_wizard/screens/progress.py
from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, ProgressBar

from r2_upload_wizard import upload
from r2_upload_wizard.models import UploadItem, UploadResult

_DONE_STATUSES = ("done", "skipped")


class ProgressScreen(Screen[None]):
    """Step 6: run the upload with live per-file and aggregate progress."""

    def compose(self) -> ComposeResult:
        yield Header()
        yield ProgressBar(id="aggregate", total=100)
        yield DataTable(id="files")
        yield Button("Cancel", id="cancel")
        yield Footer()

    def on_mount(self) -> None:
        state = self.app.state
        total_bytes = sum(item.size for item in state.items)
        table = self.query_one("#files", DataTable)
        table.add_columns("File", "Status", "Progress")
        for item in state.items:
            table.add_row(item.key, item.status, "0%", key=item.key)
        self.query_one("#aggregate", ProgressBar).update(total=total_bytes or 1, progress=0)
        self._run_upload()

    @work(thread=True)
    def _run_upload(self) -> None:
        state = self.app.state

        def on_progress(item: UploadItem) -> None:
            self.app.call_from_thread(self._on_progress, item)

        result = upload.run(
            state.client,
            state.bucket,
            state.items,
            on_progress=on_progress,
            cancel_event=state.cancel_event,
        )
        self.app.call_from_thread(self._finish, result)

    def _on_progress(self, item: UploadItem) -> None:
        table = self.query_one("#files", DataTable)
        if item.status in _DONE_STATUSES:
            percent = "100%"
        elif item.size:
            percent = f"{int(100 * item.bytes_sent / item.size)}%"
        else:
            percent = "0%"
        table.update_cell(item.key, "Status", item.status)
        table.update_cell(item.key, "Progress", percent)
        done_bytes = sum(
            (i.size if i.status in _DONE_STATUSES else i.bytes_sent) for i in self.app.state.items
        )
        self.query_one("#aggregate", ProgressBar).update(progress=done_bytes)

    @on(Button.Pressed, "#cancel")
    def _on_cancel(self) -> None:
        self.app.state.cancel_event.set()
        cancel_button = self.query_one("#cancel", Button)
        cancel_button.label = "Cancelling..."
        cancel_button.disabled = True

    def _finish(self, result: UploadResult) -> None:
        self.app.state.result = result
        self._advance()

    def _advance(self) -> None:
        from r2_upload_wizard.screens.summary import SummaryScreen

        self.app.push_screen(SummaryScreen())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_screen_progress.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/screens/progress.py tests/test_screen_progress.py
git commit -m "feat: progress screen with live per-file/aggregate progress and cancel"
```

---

### Task 19: Summary screen (results, retry-failed, upload-another, quit)

**Files:**
- Create: `src/r2_upload_wizard/screens/summary.py`
- Test: `tests/test_screen_summary.py`

**Interfaces:**
- Consumes: `WizardState.result`.
- Produces: `SummaryScreen`. Retry re-enters `ProgressScreen` with previously-failed items reset to `"pending"` (done/skipped items are untouched, since `upload.run` only processes `"pending"` items). Upload-another resets `items`/`source_path`/`result` and re-enters `SourceSelectScreen`, keeping the same bucket/client. Quit calls `self.app.exit()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_screen_summary.py
from pathlib import Path

import pytest

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.models import UploadItem, UploadResult
from r2_upload_wizard.screens.summary import SummaryScreen


class _RecordingSummaryScreen(SummaryScreen):
    def _retry(self) -> None:
        self.app.retried = True

    def _upload_another(self) -> None:
        self.app.restarted = True


class _TestApp(R2WizardApp):
    retried = False
    restarted = False

    def __init__(self, result, **kwargs):
        super().__init__(**kwargs)
        self.state.result = result

    def on_mount(self) -> None:
        self.push_screen(_RecordingSummaryScreen())


def _item(key: str, size: int, status: str = "failed", error: str | None = None) -> UploadItem:
    item = UploadItem(local_path=Path(key), relative_path=key, key=key, size=size)
    item.status = status
    item.error = error
    return item


@pytest.mark.asyncio
async def test_shows_counts_and_disables_retry_when_nothing_failed(tmp_path: Path):
    result = UploadResult(succeeded=2, skipped=1, failed=[], total_bytes=30, elapsed_seconds=1.2)
    app = _TestApp(result, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Button, Static

        summary = str(app.screen.query_one("#summary", Static).render())
        assert "Succeeded: 2" in summary
        assert app.screen.query_one("#retry", Button).disabled is True


@pytest.mark.asyncio
async def test_shows_failures_and_enables_retry(tmp_path: Path):
    failed_item = _item("bad.txt", 5, error="boom")
    result = UploadResult(
        succeeded=1, skipped=0, failed=[failed_item], total_bytes=5, elapsed_seconds=0.5
    )
    app = _TestApp(result, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Button, Static

        failures = str(app.screen.query_one("#failures", Static).render())
        assert "bad.txt" in failures
        assert "boom" in failures
        assert app.screen.query_one("#retry", Button).disabled is False


@pytest.mark.asyncio
async def test_retry_resets_failed_items_and_advances(tmp_path: Path):
    failed_item = _item("bad.txt", 5, error="boom")
    result = UploadResult(
        succeeded=1, skipped=0, failed=[failed_item], total_bytes=5, elapsed_seconds=0.5
    )
    app = _TestApp(result, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#retry")
        await pilot.pause()
        assert failed_item.status == "pending"
        assert failed_item.error is None
        assert app.retried is True


@pytest.mark.asyncio
async def test_upload_another_resets_state_and_advances(tmp_path: Path):
    result = UploadResult(succeeded=1, skipped=0, failed=[], total_bytes=5, elapsed_seconds=0.5)
    app = _TestApp(result, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#another")
        await pilot.pause()
        assert app.state.items == []
        assert app.state.result is None
        assert app.restarted is True


@pytest.mark.asyncio
async def test_quit_button_exits_without_error(tmp_path: Path):
    result = UploadResult(succeeded=1, skipped=0, failed=[], total_bytes=5, elapsed_seconds=0.5)
    app = _TestApp(result, dotenv_path=tmp_path / ".env")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#quit")
        await pilot.pause()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_screen_summary.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'r2_upload_wizard.screens.summary'`

- [ ] **Step 3: Write `screens/summary.py`**

```python
# src/r2_upload_wizard/screens/summary.py
from __future__ import annotations

import threading

from textual import on
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static


class SummaryScreen(Screen[None]):
    """Step 7: final results and what to do next."""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="summary")
        yield Static(id="failures")
        yield Button("Retry failed", id="retry", disabled=True)
        yield Button("Upload another", id="another")
        yield Button("Quit", id="quit")
        yield Footer()

    def on_mount(self) -> None:
        result = self.app.state.result
        assert result is not None
        lines = [
            f"Succeeded: {result.succeeded}",
            f"Skipped: {result.skipped}",
            f"Failed: {len(result.failed)}",
            f"Total bytes transferred: {result.total_bytes}",
            f"Elapsed: {result.elapsed_seconds:.1f}s",
        ]
        self.query_one("#summary", Static).update("\n".join(lines))
        if result.failed:
            failure_lines = [f"{item.key}: {item.error}" for item in result.failed]
            self.query_one("#failures", Static).update("\n".join(failure_lines))
            self.query_one("#retry", Button).disabled = False
        else:
            self.query_one("#failures", Static).update("")

    @on(Button.Pressed, "#retry")
    def _on_retry(self) -> None:
        state = self.app.state
        assert state.result is not None
        for item in state.result.failed:
            item.status = "pending"
            item.bytes_sent = 0
            item.error = None
        state.cancel_event = threading.Event()
        self._retry()

    def _retry(self) -> None:
        from r2_upload_wizard.screens.progress import ProgressScreen

        self.app.push_screen(ProgressScreen())

    @on(Button.Pressed, "#another")
    def _on_another(self) -> None:
        state = self.app.state
        state.items = []
        state.source_path = None
        state.result = None
        state.cancel_event = threading.Event()
        self._upload_another()

    def _upload_another(self) -> None:
        from r2_upload_wizard.screens.source_select import SourceSelectScreen

        self.app.push_screen(SourceSelectScreen())

    @on(Button.Pressed, "#quit")
    def _on_quit(self) -> None:
        self.app.exit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_screen_summary.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/r2_upload_wizard/screens/summary.py tests/test_screen_summary.py
git commit -m "feat: summary screen with retry-failed and upload-another"
```

---

### Task 20: End-to-end wizard flow tests (happy path + error paths)

**Files:**
- Create: `tests/test_wizard_flow.py`

**Interfaces:**
- Consumes: every screen module (Tasks 11-19) used unmodified — this is the first task that drives the real, unmodified screen chain end to end rather than an isolated screen with an `_advance()`/`_choose_*` override.
- The one genuinely hard-to-drive dependency, the native `FileOpen`/`SelectDirectory` modal from `textual-fspicker`, is bypassed with `monkeypatch.setattr(SourceSelectScreen, "_choose_directory", ...)` — those methods exist specifically as the override point (see Task 15), so this is the intended way to test the flow, not a workaround.

- [ ] **Step 1: Write the tests**

```python
# tests/test_wizard_flow.py
from pathlib import Path

import pytest

from r2_upload_wizard.app import R2WizardApp
from r2_upload_wizard.screens.source_select import SourceSelectScreen
from tests.fakes import FakeS3Client


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
        from textual.widgets import Button

        await pilot.click("#retry")
        await _wait_for(pilot, lambda: app.state.result is not None and app.state.result.succeeded == 1)
        assert "bad.txt" in fake_client.buckets["photos"]
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_wizard_flow.py -v`
Expected: PASS (4 tests). If any step's widget IDs don't match what an
earlier task actually produced, fix the mismatch in whichever of Tasks
11-19 is wrong (these tests are the source of truth for the full flow)
rather than papering over it here.

- [ ] **Step 3: Run the entire test suite**

Run: `uv run pytest -v`
Expected: PASS (all tests from Tasks 1-20)

- [ ] **Step 4: Commit**

```bash
git add tests/test_wizard_flow.py
git commit -m "test: end-to-end wizard flow (happy path + 3 error paths)"
```

---

### Task 21: CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: a GitHub Actions workflow running lint, format-check, and tests on every push/PR, per spec §11. Not pushed to GitHub this session (per the user's git decision), but present and correct in the repo.

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v3
        with:
          version: "latest"

      - name: Set up Python
        run: uv python install 3.11

      - name: Sync dependencies
        run: uv sync --all-groups

      - name: Lint
        run: uv run ruff check .

      - name: Format check
        run: uv run ruff format --check .

      - name: Test
        run: uv run pytest -v
```

- [ ] **Step 2: Verify the equivalent commands pass locally**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pytest -v`
Expected: all three pass (this is what CI will run; we can't execute the
GitHub Actions runner itself locally, so this is the real verification).

If `ruff format --check .` fails because earlier tasks' code isn't
ruff-formatted, run `uv run ruff format .` to fix formatting, then re-run
Step 2's full command and commit the formatting fixes as part of this
task's commit.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: lint, format-check, and test workflow"
```

---

### Task 22: Documentation

**Files:**
- Create: `README.md`
- Create: `AGENTS.md`
- Create: `SECURITY.md`
- Create: `CONTRIBUTING.md`
- Create: `CHANGELOG.md`
- Create: `LICENSE`

**Interfaces:**
- Produces: the full doc set from spec §12. `CONTRIBUTING.md` is the GitHub-standard name used instead of the user's original "CONTRIBUTE.md" (documented in the spec's decisions table).

- [ ] **Step 1: Write `README.md`**

```markdown
# R2 Upload Wizard

A polished, installable [Textual](https://textual.textualize.io/) terminal
wizard for uploading a single file or an entire directory tree to a
[Cloudflare R2](https://developers.cloudflare.com/r2/) bucket -- credential
setup, bucket create/select/delete, source picking, a full preview, live
progress, and a final summary, with no external binaries (no rclone/wrangler)
required.

## Install

```bash
uv tool install .
# or
pipx install .
```

This installs the `r2-wizard` command.

## Quickstart

```bash
r2-wizard
```

On first run, the wizard checks for the 5 Cloudflare R2 environment
variables below. Any missing or invalid one gets an inline prompt right
there -- fill it in once and it's saved to `.env` in your current directory
for next time.

| Variable | Required | Purpose |
|---|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | Yes | Your Cloudflare account ID |
| `CLOUDFLARE_ACCESS_KEY_ID` | Yes | R2 API token access key ID |
| `CLOUDFLARE_SECRET_ACCESS_KEY` | Yes | R2 API token secret |
| `CLOUDFLARE_S3_URL` | Yes | R2 S3-compatible endpoint, e.g. `https://<account id>.r2.cloudflarestorage.com` |
| `CLOUDFLARE_API_TOKEN` | Tracked, not yet used | Reserved for future Cloudflare-API-based features |

See `.env.example` for a template. Create an R2 API token and find your
account ID in the Cloudflare dashboard under **R2 > Manage R2 API Tokens**.

## Features

- **Bucket management**: list, create, and delete buckets straight from the
  wizard (delete requires typing the bucket name to confirm, and refuses to
  delete a non-empty bucket).
- **File or directory upload**: pick a single file, or a whole directory
  (relative paths are preserved as object keys).
- **Destination prefix**: optional path/prefix on the bucket, with a live
  preview of the resulting object keys.
- **Full preview before anything uploads**: source, file count, total size,
  destination, and (for directories) how many destination keys already
  exist, with a per-run choice to skip or overwrite them.
- **Live progress**: aggregate and per-file progress, cancelable mid-run.
- **Resilient batches**: one failed file never aborts the rest; the summary
  screen lists failures and can retry just those.

## Keyboard shortcuts

- `Escape` -- back a step
- `y` / `n` -- confirm / go back on the confirmation screen
- `r` -- retry loading buckets after an error
- `n` / `d` -- create / delete a bucket, from the bucket select screen

## Troubleshooting

- **"check your Access Key ID / Secret"**: the R2 API token's key pair is
  wrong or has been revoked -- fix it on the setup screen.
- **Bucket list fails with a connection error**: check `CLOUDFLARE_S3_URL`
  and your network.
- **Deleting a bucket is refused**: R2 (like S3) requires a bucket to be
  empty before it can be deleted; the wizard shows the object count and
  does not offer to auto-empty it.

## Development

See `CONTRIBUTING.md`.

## License

MIT -- see `LICENSE`.
```

- [ ] **Step 2: Write `AGENTS.md`**

```markdown
# AGENTS.md

Guidance for AI coding agents (and humans) working in this repository.

## What this is

A Textual TUI (`r2-wizard`) that uploads files/directories to Cloudflare R2
over its S3-compatible API via boto3. No external binaries.

## Repo map

- `src/r2_upload_wizard/models.py` -- dataclasses shared by every module.
- `src/r2_upload_wizard/config.py` -- env var detection/validation/persistence. No Textual imports.
- `src/r2_upload_wizard/keys.py` -- destination-prefix + relative-path -> object key. No Textual imports.
- `src/r2_upload_wizard/r2_client.py` -- boto3 client factory and thin wrappers (list/create/delete bucket, head object). No Textual imports.
- `src/r2_upload_wizard/upload.py` -- the threaded upload engine (planning + execution). No Textual imports.
- `src/r2_upload_wizard/app.py` -- `R2WizardApp` and `WizardState`, the only place that owns cross-screen state.
- `src/r2_upload_wizard/screens/` -- one module per wizard step. Screens are the *only* place allowed to touch widgets; all real logic lives in the modules above.
- `tests/fakes.py` -- `FakeS3Client`, an in-memory stand-in for a boto3 S3 client used throughout the test suite so nothing needs real network or credentials.

## Running things locally

```bash
uv sync --all-groups
uv run r2-wizard          # run the app
uv run pytest             # run tests
uv run ruff check .       # lint
uv run ruff format .      # format
```

## Conventions

- Logic modules (`config.py`, `keys.py`, `r2_client.py`, `upload.py`,
  `models.py`) stay UI-free and unit-testable without a running Textual app.
- Screens delegate to those modules; a screen file should read as "wire up
  widgets, call a function, react to the result."
- Background work (bucket listing, directory scanning, uploads) runs in
  `@work(thread=True)` workers; touch widgets only via
  `self.app.call_from_thread(...)`, never directly from a worker thread.
- Cross-screen navigation forward (`self.app.push_screen(NextScreen())`) is
  wrapped in a small `_advance()` (or similarly named) method on the screen
  specifically so tests can override just that one method without needing
  the next screen's module to exist or be driven.
- New screens import the *next* screen lazily, inside the method that
  pushes it, to avoid import cycles across the screen package.

## Never do this

- Never log, print, or include in an error message the value of
  `CLOUDFLARE_SECRET_ACCESS_KEY`, `CLOUDFLARE_ACCESS_KEY_ID`, or
  `CLOUDFLARE_API_TOKEN`. The setup screen only ever shows a masked preview
  (last 4 characters).
- Never let an unhandled exception surface as a raw traceback in the TUI --
  catch it at the screen boundary and show a short message instead.
- Never auto-empty a bucket to satisfy a delete request. A non-empty
  bucket's delete is refused, full stop.
- Never call `CLOUDFLARE_API_TOKEN` against any API -- it's tracked/validated
  for shape only in this version.

## Testing approach

- Unit tests for the logic modules use `botocore.stub.Stubber` (for
  `r2_client.py`) or plain function calls (for `config.py`/`keys.py`/
  `upload.py`'s planning logic) -- no real network.
- Screen tests use Textual's `Pilot` (`app.run_test()`), usually subclassing
  the screen under test to override its `_advance()`/`_choose_*` method so
  the test doesn't need every downstream screen to exist.
- `tests/test_wizard_flow.py` drives the real, unmodified screen chain end
  to end against `FakeS3Client`, monkeypatching only the native
  file-picker dependency.
```

- [ ] **Step 3: Write `SECURITY.md`**

```markdown
# Security Policy

## What this tool can do with your credentials

`r2-wizard` uses the R2 API token you provide (`CLOUDFLARE_ACCESS_KEY_ID` /
`CLOUDFLARE_SECRET_ACCESS_KEY`) to call R2's S3-compatible API directly:
list, create, and delete buckets, and upload/HEAD objects, scoped to
whatever permissions that token was issued with. `CLOUDFLARE_API_TOKEN` is
detected and validated for shape but is **not** called against any API in
this version.

## Credential handling

- Credentials are read from your shell environment first, then from a
  `.env` file in the current directory. A value already exported in your
  shell always wins over `.env`.
- The setup screen never displays a full secret value -- only a masked
  preview (all but the last 4 characters starred out).
- Values you type into the setup screen are written to `.env` in your
  current directory, never elsewhere, and never logged.
- **`.env` is not committed** -- it's excluded via `.gitignore`. Treat it
  like any other secret file: don't paste it into chat, issues, or CI logs.
- Set restrictive permissions on your `.env` file if you're on a shared
  machine, e.g. `chmod 600 .env`.

## Scope of destructive actions

- Bucket **delete** requires typing the bucket's name to confirm and is
  refused outright if the bucket is not empty -- this tool never empties a
  bucket on your behalf.
- Upload **overwrite** only happens when you explicitly choose
  "Overwrite all" on the confirmation screen for a given run; the default
  is to skip files that already exist at the destination with a matching
  size.

## Reporting a vulnerability

If you find a security issue in this project, please open a private
security advisory on the repository (GitHub's "Report a vulnerability"
under the Security tab) rather than a public issue, so it can be addressed
before details are public. Include reproduction steps and the affected
version.
```

- [ ] **Step 4: Write `CONTRIBUTING.md`**

```markdown
# Contributing

## Dev setup

```bash
git clone <this repo>
cd r2-upload-wizard
uv sync --all-groups
```

## Running tests and lint

```bash
uv run pytest -v          # full test suite
uv run pytest -v -k name  # a subset
uv run ruff check .       # lint
uv run ruff format .      # format
```

All of these are also run in CI (`.github/workflows/ci.yml`) on every push
and pull request.

## Code style

- Logic modules stay free of Textual imports; screens stay free of real
  logic (see `AGENTS.md` for the full breakdown).
- No unhandled exception should ever reach the TUI as a raw traceback.
- New features that touch credentials must not introduce any new way to
  log, print, or otherwise leak a secret value -- see `SECURITY.md`.

## Making a change

1. Write a failing test first where practical (see existing tests for the
   house style -- most logic is tested without any real network, and
   screens are tested with Textual's `Pilot` against `tests/fakes.py`'s
   `FakeS3Client`).
2. Keep commits scoped to one logical change.
3. Run the full suite (`uv run pytest`) and lint (`uv run ruff check .`)
   before opening a PR.
4. Update `CHANGELOG.md` under "Unreleased" for any user-facing change.

## Pull requests

Describe what changed and why. Link any relevant issue. Small, focused PRs
are easier to review than large ones.
```

- [ ] **Step 5: Write `CHANGELOG.md`**

```markdown
# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - Unreleased

### Added

- Initial release: Textual wizard for uploading a file or directory to
  Cloudflare R2, with credential setup, bucket create/select/delete,
  destination prefix, a full preview before upload, live progress, and a
  summary with retry-failed and upload-another.
```

- [ ] **Step 6: Write `LICENSE`**

```
MIT License

Copyright (c) 2026 R2 Upload Wizard Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 7: Verify the install instructions actually work**

Run: `uv tool install --force --editable . && r2-wizard --help 2>&1 | head -5; uv tool uninstall r2-upload-wizard`
Expected: the tool installs and the `r2-wizard` command exists (a Textual
app has no real `--help` output by default, so don't assert on its
content -- just confirm the command is found and starts). Note this
temporarily installs the in-development package globally via `uv tool`
and immediately uninstalls it; if the sandbox disallows global installs,
skip running this and instead confirm equivalently with
`uv run r2-wizard &` backgrounded briefly, then killed -- the goal is only
to prove the entry point wiring from Task 1 is correct, not to leave
anything installed.

- [ ] **Step 8: Commit**

```bash
git add README.md AGENTS.md SECURITY.md CONTRIBUTING.md CHANGELOG.md LICENSE
git commit -m "docs: README, AGENTS, SECURITY, CONTRIBUTING, CHANGELOG, LICENSE"
```

---

## Manual QA (not automated, do together with the user)

The automated suite (Tasks 1-20) covers all logic and screen behavior
against fakes. It intentionally never talks to real Cloudflare R2 --
creating/deleting real buckets and uploading real objects with the
credentials in this repo's `.env` is a consequential, real-money-and-data
action that should happen with the user watching, not as an unattended
step in a subagent's task list. Once the plan above is fully implemented:

1. Run `uv run r2-wizard` with the real `.env` in this repo.
2. Walk through creating a small scratch bucket, uploading one small test
   file to it, confirming it's retrievable (e.g. via the Cloudflare
   dashboard or `head_object`), and then deleting that scratch bucket
   (after emptying it) to leave no residue.
3. Confirm the acceptance criteria in spec §13 that aren't already covered
   by the automated suite: real network interruption doesn't crash the
   app, and a real large-ish directory upload's progress bar tracks
   correctly.

Do not run this step unattended or as part of automated task execution.

---

## Self-review

**Spec coverage:**

- §3 decisions (engine, packaging, credential setup, existing-key
  handling, concurrency, git, license, bucket create/delete) -- all
  implemented (Tasks 1, 6-9, 11, 17, 12-14) and reflected in Global
  Constraints.
- §4 non-goals -- respected: no lifecycle/CORS, no auto-empty-on-delete
  (Task 7/14), no download, no multi-profile, no bandwidth UI, no
  cross-restart part-resume, `CLOUDFLARE_API_TOKEN` never called (Task 3
  only validates its shape, no API use anywhere).
- §5 package layout -- matches Tasks 1, 10 exactly, file for file.
- §6 screen flow steps 1-7 -- Tasks 11, 12-14, 15, 16, 17, 18, 19
  respectively.
- §7 data models -- Task 2, used verbatim by every later task.
- §8 env var validation rules -- Task 3.
- §9 upload engine (checksum config, TransferConfig, worker pool,
  content-type, existing-key check, cancellation, retry-via-botocore) --
  Tasks 6, 8, 9, 17.
- §10 error handling -- friendly error mapping in Tasks 12 (bucket list),
  13 (create), 14 (delete), 19 (per-file failures shown, batch never
  aborted per Task 9's design); no raw tracebacks anywhere widgets are
  touched only after a `try`/`except` at the worker boundary.
- §11 testing strategy -- unit tests in Tasks 3, 4, 5, 6, 7, 8, 9;
  integration tests in Task 20; CI in Task 21.
- §12 documentation deliverables -- Task 22, all six files, with the
  `CONTRIBUTE.md` -> `CONTRIBUTING.md` substitution called out.
- §13 acceptance criteria -- covered by Task 20's automated suite except
  the two genuinely-needs-a-human items, which are called out explicitly
  in "Manual QA" above rather than silently dropped.

**Placeholder scan:** no "TBD"/"TODO"/"similar to Task N"/unshown code
remain; every step has real, complete code. The one intentionally
unresolved reference (Task 11's `bucket_select` import before Task 12
exists) is explicitly explained and handled via the override-point test
pattern, not left as a dangling assumption.

**Type consistency:** `UploadItem`/`EnvVarStatus`/`BucketInfo`/`UploadPlan`/
`UploadResult` field names are used identically from their Task 2
definition through Tasks 6-20. `WizardState` attribute names introduced in
Task 10 (`dotenv_path`, `env`, `client`, `bucket`, `source_path`,
`source_mode`, `items`, `prefix`, `overwrite_existing`, `cancel_event`,
`result`) are used with those exact names in every later screen task.
`r2_client` function names (`build_client`, `list_buckets`,
`head_object_size`, `validate_bucket_name`, `is_bucket_empty`,
`create_bucket`, `delete_bucket`) and `upload` function/constant names
(`TRANSFER_CONFIG`, `DEFAULT_MAX_WORKERS`, `guess_content_type`,
`plan_items`, `run`) are each defined once and referenced identically
everywhere else.

---

**Plan complete and saved to `docs/superpowers/plans/2026-09-01-r2-upload-wizard.md`.** Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
