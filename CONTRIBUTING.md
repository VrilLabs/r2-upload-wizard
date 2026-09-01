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
