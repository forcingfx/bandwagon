# Contributing

## Set up

```sh
uv sync                  # a .venv with the app, pytest and ruff
uv run gtd               # the app from the checkout
uv run pytest -q         # every test; tests/test_app.py drives the TUI headlessly
uv run ruff check .      # syntax, undefined names, unused imports
```

CI runs the same two commands on Linux and macOS, on Python 3.11 and 3.14. A pull
request needs them green.

## Commits

One change per commit, with a subject that says what changed for the person using it:
`feat:`, `fix:`, `docs:`, `build:`, `ci:`, `test:` or `refactor:`, then a plain sentence.
For a fix, the body says what happened, why, and what now stops it.

## Releasing

1. Set `__version__` in `gtd/__init__.py` and commit it as `build: release vX.Y.Z`.
2. Tag and push: `git tag -a vX.Y.Z -m vX.Y.Z && git push && git push --tags`.
3. Publish the notes: `gh release create vX.Y.Z --generate-notes`.

Users on `uv tool install git+https://github.com/forcingfx/bandwagon` pick it up with
`uv tool upgrade bandwagon`, or pin with `@vX.Y.Z`.
