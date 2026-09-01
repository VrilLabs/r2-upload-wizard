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
