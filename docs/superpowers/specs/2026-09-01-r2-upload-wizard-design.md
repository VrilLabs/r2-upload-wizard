# R2 Upload Wizard — Design Spec

Date: 2026-09-01
Status: Approved for planning

## 1. Purpose

A polished, installable Textual TUI that walks a user through uploading a single
file or an entire directory tree to a Cloudflare R2 bucket: detect/collect
credentials, pick a bucket from the account, pick a local source, pick a
destination prefix, preview the exact action, confirm, upload with live
progress, and report results. Intended as a long-term, reusable tool — not a
one-off script — for any R2 bucket on the configured account.

## 2. Background / competitive scan

- No existing Textual-based R2/S3 upload wizard was found on GitHub or PyPI.
  `botree` is the closest hit but is a plain boto3 wrapper library with no TUI.
- Existing rclone TUIs (`darkhz/rclone-tui`, `miklos-szel/rclone-commander`)
  are general-purpose Go rclone managers, not upload wizards, but confirm two
  useful UX patterns worth borrowing: per-file progress rows during parallel
  transfer, and dual real-time global/per-file stats.
- `textual-fspicker` (davep) is the de facto Textual filesystem picker
  (`FileOpen`, `SelectDirectory`) — use it rather than building one.
- `docs/Cloudflare R2 Bulk Upload via rclone - A Guide (2026).md` (already in
  this repo) documents Cloudflare's own guidance: wrangler cannot bulk-upload
  and caps at 315 MB/object; rclone is the documented path for real bulk
  transfers; and gives concrete cost-optimized multipart tuning (64 MiB chunk
  size, 256 MiB upload cutoff, 4-way part concurrency, 16 parallel transfers)
  used as the reference point for this tool's default upload tuning.
- boto3's `s3.transfer.TransferConfig` reimplements everything rclone brings
  for this use case — multipart with configurable chunk size/concurrency,
  automatic threshold-based single-vs-multipart selection, and a `Callback`
  hook for byte-level progress — natively in Python, with no external binary
  dependency. R2's S3-compatible endpoint supports `ListBuckets` via boto3
  (`region_name="auto"`, endpoint = `CLOUDFLARE_S3_URL`).

## 3. Decisions (locked)

| Decision | Choice | Why |
|---|---|---|
| Upload engine | Pure boto3 (`boto3.client("s3", endpoint_url=..., ...)`) | Self-contained, no external binary; native progress callbacks; matches R2's documented cost-tuning knobs |
| Packaging | `pyproject.toml`, `uv`/`pipx`-installable CLI (`r2-wizard`) | "Long-term reliable solution" implies a real installable tool, not a script you `cd` into |
| Credential setup | Inline editable setup screen, writes back to `.env` | Turns the tool into a true onboarding wizard, not just a status check |
| Bulk existing-key handling | Ask per-run on the confirmation screen (skip-existing vs overwrite-all) | Different runs have different intents (top-up vs. full re-sync); don't hardcode one |
| Bulk concurrency | Parallel file uploads via worker pool (default 8) | Typical bulk case is many small/medium files where per-file multipart parallelism alone is not enough |
| Git | `git init` + local commits only, no GitHub push yet | User's explicit choice this session |
| License | MIT | User's explicit choice this session |
| Bucket create/delete | In scope, on the Bucket select screen, both gated by explicit confirmation | User's explicit choice this session — see §6 step 2 |
| Delete of a non-empty bucket | Refused, with the object count shown, rather than cascade-deleting objects | R2's `DeleteBucket` requires an empty bucket anyway; auto-emptying a bucket is a much larger blast radius than "delete a bucket" implies and wasn't asked for |

## 4. Non-goals (v1)

- Bucket lifecycle/CORS/other bucket-level configuration (storage class,
  public access, custom domains, notifications). Only **create** and
  **delete** are in scope for buckets themselves.
- Emptying a non-empty bucket as part of delete (see decisions table above)
  — deleting a non-empty bucket is refused, not auto-cascaded.
- Download / sync-back from R2.
- Multiple named credential profiles / multi-account switching.
- Bandwidth throttling UI (tunable in code/config, not exposed as a wizard step).
- Resuming a single large multipart upload across process restarts (a
  skip-if-exists check across *files* is in scope; resuming a partially
  uploaded *part* of one file is not).
- Using `CLOUDFLARE_API_TOKEN` for anything functional in v1 — it is
  detected/validated for shape but not called against the Cloudflare REST
  API. Reserved for future bucket-management features.

## 5. Package layout

```
r2-upload-wizard/
├── pyproject.toml
├── README.md
├── AGENTS.md
├── SECURITY.md
├── CONTRIBUTING.md
├── CHANGELOG.md
├── LICENSE                      # MIT
├── .gitignore
├── .env.example
├── docs/
│   └── superpowers/specs/...    # this file and future specs
├── src/
│   └── r2_upload_wizard/
│       ├── __init__.py
│       ├── __main__.py          # `python -m r2_upload_wizard`
│       ├── app.py               # R2WizardApp: screen stack, global state
│       ├── models.py            # dataclasses (see §7)
│       ├── config.py            # env var detection, validation, .env persistence
│       ├── r2_client.py         # boto3 client factory, list/create/delete bucket, head_object
│       ├── upload.py            # upload engine: planning, worker pool, progress events
│       ├── keys.py              # local path -> object key mapping
│       └── screens/
│           ├── __init__.py
│           ├── setup.py         # env var status + inline edit
│           ├── bucket_select.py
│           ├── source_select.py
│           ├── destination.py
│           ├── confirm.py
│           ├── progress.py
│           └── summary.py
└── tests/
    ├── test_config.py
    ├── test_keys.py
    ├── test_upload_planning.py
    ├── test_r2_client.py
    └── test_wizard_flow.py       # Pilot-driven end-to-end screen tests
```

## 6. Screen flow (state machine)

Each screen can go **Back** (except Setup, which is the root) and the app
holds a single mutable `WizardState` passed screen-to-screen (not global
mutable module state, to keep screens testable in isolation).

1. **Setup** (`screens/setup.py`)
   - On mount, calls `config.detect_env()` which returns an `EnvVarStatus`
     per variable: `present_valid`, `present_invalid(reason)`, or `missing`,
     and *where* it came from (`process env` vs `.env file`).
   - Renders a table: name, status icon (✓ / ✗ / ⚠), source, masked value
     preview for secrets (`sk_...ab12`, last 4 chars only).
   - Any `missing`/`invalid` required var gets an inline `Input` (masked via
     `password=True` for `SECRET_ACCESS_KEY` and `API_TOKEN`) to fill in.
   - "Continue" is disabled until `ACCOUNT_ID`, `ACCESS_KEY_ID`,
     `SECRET_ACCESS_KEY`, and `S3_URL` are all `present_valid`. `API_TOKEN`
     is shown but never blocks continuation.
   - On Continue with edits present, `config.persist(changed_vars)` writes
     them into `.env` (creating the file with header comment if absent,
     preserving unrelated existing lines) and updates `os.environ` for the
     running process.
   - Validation rules (§8) run on every keystroke-debounced change so the
     status icon updates live.

2. **Bucket select** (`screens/bucket_select.py`)
   - On mount: background worker calls `r2_client.list_buckets()`.
   - Loading spinner while pending; on success, a selectable `ListView` of
     bucket names (+ creation date if the API returns it); on failure, an
     error banner with the boto3 error message and **Retry** / **Back**
     actions (common cases mapped to friendly text: `InvalidAccessKeyId`,
     `SignatureDoesNotMatch`, network/DNS failure).
   - Selecting a bucket stores it on `WizardState` and advances.
   - **Create bucket** action (`n` key / button): opens an inline name
     input. Cloudflare R2 bucket-name rules are validated client-side before
     the call (lowercase letters, digits, hyphens; 3–63 chars; must
     start/end alphanumeric) so bad input never round-trips to the API.
     `BucketAlreadyExists` / `BucketAlreadyOwnedByYou` are mapped to a
     friendly "that name's taken, try another" message rather than a raw
     error. On success the list refreshes, the new bucket is auto-selected,
     and the wizard advances directly to Source select — you just created
     it to use it.
   - **Delete bucket** action (`d` key / button, on the highlighted bucket):
     - First checks whether the bucket is empty (a single `list_objects_v2`
       call with `MaxKeys=1`). If it is **not** empty, deletion is refused
       with a message showing the (approximate, "1+" if paginated) object
       count and no further action is offered — see §4.
     - If empty, requires typing the bucket name to confirm (not just
       Y/N — this is a destructive, unrecoverable action) before calling
       `delete_bucket`.
     - On success, the list refreshes and stays on this screen (nothing to
       advance to). On failure, an error banner as above.

3. **Source select** (`screens/source_select.py`)
   - A mode toggle: **File** / **Directory**.
   - Uses `textual_fspicker.FileOpen` or `SelectDirectory` rooted at the
     user's home (or last-used dir, remembered in-session only, not
     persisted across runs in v1).
   - On selection:
     - File mode: immediately show its size.
     - Directory mode: kick off a background worker (`app.run_worker`) that
       walks the tree (`os.walk`, following no symlinks by default) and
       streams `UploadItem(local_path, relative_path, size)` into
       `WizardState.items`, updating a live "N files, X MB found…" counter
       so the UI stays responsive on large trees. A **Cancel scan** action
       is available while scanning.
   - Both modes: a "Continue" button appears once at least one item is
     known.

4. **Destination** (`screens/destination.py`)
   - Single text `Input` for the optional key prefix (default empty = root).
     Leading/trailing slashes normalized (stripped, then a single trailing
     `/` appended internally if non-empty, per `keys.py` rules).
   - Live preview area shows the resulting key for up to the first 5 items
     via `keys.build_key(prefix, relative_path)`.

5. **Confirm** (`screens/confirm.py`)
   - On mount, if bulk mode: background worker `HEAD`s each destination key
     (bounded concurrency, e.g. 16 at a time) to find how many already exist
     with an identical size (treated as "already uploaded"); shows a spinner
     with a running count while this check is in flight, with a **Skip
     check / assume none exist** escape hatch for very large trees.
   - Renders the full preview:
     - Source path and mode (file/directory)
     - File count and total size (human-readable)
     - Destination bucket and prefix
     - "`N` of `M` destination keys already exist" (bulk only)
     - If any exist: a choice — **Skip existing** (default) or
       **Overwrite all**
   - Y/N confirmation (`y`/`n` keys and buttons). N returns to Destination.

6. **Progress** (`screens/progress.py`)
   - On confirm, `upload.run(plan, on_event)` is started in a background
     worker. `plan` already reflects skip-existing filtering decided on the
     previous screen.
   - UI shows:
     - Aggregate progress bar: bytes transferred / total bytes, with a
       transfer-rate readout.
     - A scrolling table of in-flight/recently-finished files with
       per-file percentage and status (uploading / done / failed / skipped).
   - **Cancel** stops scheduling new files; in-flight ones are allowed to
     finish (or are aborted, per §9) and the screen transitions to Summary
     with whatever completed.
   - Progress events arrive via Textual `Message` posted from worker threads
     (`self.post_message(...)` is thread-safe in Textual); the screen never
     touches widgets directly from a non-UI thread.

7. **Summary** (`screens/summary.py`)
   - Counts: succeeded / skipped / failed, total bytes transferred, elapsed
     time.
   - If any failed: a list with reason per file and a **Retry failed**
     action that re-enters Progress with just those items.
   - Actions: **Upload another** (returns to Source select, keeping the same
     bucket/creds) or **Quit**.

## 7. Data models (`models.py`)

```python
@dataclass
class EnvVarStatus:
    name: str
    value: str | None          # None if missing
    source: Literal["process_env", "dotenv", "missing"]
    valid: bool
    reason: str | None         # populated when invalid

@dataclass
class BucketInfo:
    name: str
    creation_date: datetime | None

@dataclass
class UploadItem:
    local_path: Path
    relative_path: str          # "" for single-file mode
    key: str                    # computed once prefix is known
    size: int
    status: Literal["pending", "uploading", "done", "skipped", "failed"] = "pending"
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

`WizardState` (in `app.py`) is a plain mutable object holding: env status,
boto3 client, selected bucket, source path/mode, discovered items, prefix,
plan, and last result — passed to each `Screen` at push time.

## 8. Env var validation rules (`config.py`)

| Var | Required | Validation |
|---|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | Yes | Non-empty, hex-like (`^[a-f0-9]{32}$` typical Cloudflare account ID shape — warn, don't hard-fail, if it doesn't match, since format isn't publicly guaranteed) |
| `CLOUDFLARE_ACCESS_KEY_ID` | Yes | Non-empty |
| `CLOUDFLARE_SECRET_ACCESS_KEY` | Yes | Non-empty |
| `CLOUDFLARE_S3_URL` | Yes | Non-empty, `urlparse` succeeds, scheme is `https`, host ends with `.r2.cloudflarestorage.com` |
| `CLOUDFLARE_API_TOKEN` | No (tracked only) | Non-empty if present; no format check |

Detection order: `os.environ` first, then `.env` in the current working
directory (loaded without overriding already-exported process env — a var
exported in the shell always wins, matching standard `.env` semantics).
`.env` parsing is hand-rolled (simple `KEY=VALUE` / `export KEY=VALUE` line
parser) to avoid pulling in `python-dotenv` for a handful of lines and to
guarantee round-trip-safe rewriting of the file (preserve comments/blank
lines/ordering; append changed keys if not already present).

## 9. Upload engine (`upload.py`)

- boto3 client: `boto3.client("s3", endpoint_url=<S3_URL>, aws_access_key_id=..., aws_secret_access_key=..., region_name="auto")`.
- **Checksum config caveat:** boto3 ≥1.36 changed default checksum behavior
  in a way that's currently incompatible with R2. The client factory in
  `r2_client.py` explicitly sets
  `Config(request_checksum_calculation="when_required", response_checksum_validation="when_required")`
  so the tool works correctly on current and future boto3 versions rather
  than pinning to an old release.
- `TransferConfig` defaults (the doc's "Balanced" profile):
  `multipart_chunksize=64*1024*1024`, `multipart_threshold=256*1024*1024`,
  `max_concurrency=4` (parts-within-a-file), `use_threads=True`.
- File-level parallelism: a `ThreadPoolExecutor(max_workers=8)` (default;
  not exposed in the UI in v1, constant in code) submits one
  `upload_file(...)` call per `UploadItem`, each with its own `Callback`
  that computes delta bytes and posts a `ProgressUpdate` message.
- Content-Type: guessed via `mimetypes.guess_type` and passed as
  `ExtraArgs={"ContentType": ...}` when resolvable; omitted otherwise (S3
  API default applies).
- Existing-key check (Confirm screen): `head_object`, compare
  `ContentLength` to local size; treated as "exists" only on an exact size
  match (a differing size is treated as *not* existing, so it re-uploads —
  avoids silently skipping a changed file with the same name).
- Cancellation: a `threading.Event` checked before each item is submitted to
  the pool; already-submitted boto3 calls are not force-aborted (boto3 has
  no clean mid-transfer cancel) but no *new* ones start, and the screen
  shows "Finishing N in-flight uploads…" before moving to Summary.
- Retry: botocore's default retry config (adaptive, several attempts) covers
  transient network errors per part; a hard failure after those retries is
  recorded on the `UploadItem` with `error = str(exc)` and does not stop the
  run.

## 10. Error handling

- No unhandled exception should ever surface as a raw Python traceback in
  the TUI. Screens wrap their background-worker bodies in `try/except`,
  translating boto3 `ClientError`/`EndpointConnectionError`/etc. into short
  user-facing messages, with the raw exception available behind a
  "show details" toggle for debugging.
- Common boto3 error codes get friendly copy: `InvalidAccessKeyId`,
  `SignatureDoesNotMatch` → "check your Access Key ID / Secret" and offer a
  **Back to Setup** shortcut; `NoSuchBucket` → re-run bucket list;
  connection errors → check `CLOUDFLARE_S3_URL` / network;
  `BucketAlreadyExists` / `BucketAlreadyOwnedByYou` → "name's taken, try
  another"; `BucketNotEmpty` (or the pre-flight non-empty check in §6 step
  2) → show object count, refuse deletion, no auto-empty offered.
- Per-file upload failures never abort the batch — see §9.

## 11. Testing strategy

- **Unit** (`tests/test_config.py`, `test_keys.py`, `test_upload_planning.py`,
  `test_r2_client.py`): pure-function/logic tests with no real network —
  env parsing/validation/persistence (using `tmp_path` for `.env`), key
  construction from prefix + relative path, `TransferConfig` selection,
  skip-existing decision logic. `r2_client` tested against a mocked boto3
  client (either `moto`'s S3 mock pointed at a fake endpoint, or a hand
  rolled stub — `moto` preferred if it proves compatible with a custom
  `endpoint_url`; otherwise a stub `botocore` client via `botocore.stub.Stubber`).
- **Integration** (`tests/test_wizard_flow.py`): Textual's `Pilot` harness
  driving `R2WizardApp` end-to-end through a happy path (valid env → pick
  bucket → pick a small temp directory → confirm → progress → summary) with
  the R2 client dependency-injected as a fake, plus at least: missing-creds
  path, bucket-list-failure path, one failed-file-in-batch path.
- **CI**: `.github/workflows/ci.yml` running `uv sync`, `uv run ruff check`,
  `uv run ruff format --check`, `uv run pytest` on push/PR to any branch
  (ships now even though we aren't pushing to GitHub yet this session).

## 12. Documentation deliverables

- **README.md** — what it is, install (`uv tool install .` /
  `pipx install .`), quickstart GIF/screenshot placeholder, full env var
  table, feature list, keyboard shortcuts, troubleshooting (mirrors §10's
  friendly error copy), license badge.
- **AGENTS.md** — repo map, how to run/test locally (`uv sync`,
  `uv run pytest`, `uv run r2-wizard`), conventions (dataclasses in
  `models.py`, screens are the only place allowed to touch widgets,
  `upload.py`/`r2_client.py`/`config.py` stay UI-free and unit-testable),
  and an explicit rule: never log or print secret values (access key,
  secret key, API token) anywhere, including in error messages.
- **SECURITY.md** — credential handling (masked in UI, `.env` never
  committed, file permissions note), scope of what this tool can do with
  the provided credentials, how to report a vulnerability.
- **CONTRIBUTING.md** — dev setup (`uv sync`), running tests/lint, branch
  conventions, PR expectations. (User said "CONTRIBUTE.md"; using GitHub's
  conventional `CONTRIBUTING.md` name so it auto-links in the repo sidebar.)
- **CHANGELOG.md** — Keep a Changelog format, starting at `0.1.0 — Unreleased`.
- **.env.example** — the 5 var names, no values, one-line comment each.
- **.gitignore** — `.env`, `__pycache__/`, `.venv/`, `*.egg-info/`, build
  artifacts, `.pytest_cache/`, OS cruft (`.DS_Store`).

## 13. Acceptance criteria

- `uv tool install .` (or `uv run r2-wizard` in dev) launches the wizard.
- With valid `.env`, the full happy path (bucket select → single file
  upload → confirm → progress → summary) completes and the file is
  retrievable at the expected key.
- A directory with mixed file sizes uploads correctly, preserving relative
  paths as keys under the chosen prefix, with parallel progress visible.
- Re-running the same bulk upload with **Skip existing** selected uploads
  zero bytes for unchanged files and correctly re-uploads any changed
  (different-size) file.
- Deleting/corrupting one env var and relaunching shows it as ✗/⚠ on Setup,
  is fixable inline, and the fix persists to `.env` for the next run.
- Creating a bucket with a valid new name succeeds, auto-selects it, and the
  wizard advances to Source select; a taken/invalid name shows a friendly
  error and stays on the screen.
- Deleting an empty bucket requires typing its name and then succeeds,
  refreshing the list; attempting to delete a non-empty bucket is refused
  with the object count shown and nothing is deleted.
- Killing network mid-run (or pointing at a bad endpoint) never crashes the
  app; it's reported per-file or as a screen-level retryable error.
- `uv run pytest` and `uv run ruff check` both pass in CI.
