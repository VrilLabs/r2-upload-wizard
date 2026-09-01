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
