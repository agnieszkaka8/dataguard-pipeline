# Repository Guidelines

DataGuard is a typed Python 3.12 CLI that syncs records between two relational databases — validating each record against a JSON rules file, writing only valid records to the target, and logging every rejection to Supabase with the exact failure reason. Stack: uv + typer + rich + psycopg2-binary + supabase. See `@context/foundation/prd.md` for full requirements.

## Hard Rules

- **Write order is absolute**: always write rejection/error records to Supabase before committing valid records to Target DB. If the Supabase write fails, abort the entire run — Target DB must never be ahead of the audit trail.
- **No credential or field-value leakage**: connection strings and raw record field values must never appear in console output, error messages, or local log files. Rejected records go only to the Supabase log.
- **`context/` is not a Python package**: never add `__init__.py` to `context/` or any subdirectory. `pyproject.toml` limits package discovery to `dataguard*` — importing from `context/` breaks the build.
- **Respect `NO_COLOR`**: all `rich` output must check the `NO_COLOR` environment variable so piped/CI output stays machine-readable.
- **Exit non-zero on every failure**: connection errors, validation aborts, and Supabase write failures must all produce a non-zero exit code. Never swallow exceptions silently.
- **Every record exits with a classification**: valid, invalid, or errored — no silent drops.

## Project Structure

Source lives in `dataguard/`; `dataguard/cli.py` is the real CLI entry point (registered in `@pyproject.toml` under `[project.scripts]`). The `main.py` at the repo root is a uv bootstrap stub — do not treat it as the entry point. Runtime state lives in `.env` (connection strings, never committed) and `.watermark` (last successful run timestamp). Validation rules are passed via `--rules <path>` at invocation time.

## Build, Test, and Development Commands

- `uv run dataguard sync --table <name> --rules <file>` — live sync
- `uv run dataguard sync --dry-run --table <name> --rules <file>` — validate without writing
- `uv run pytest` — run the test suite
- `uv run mypy dataguard/` — type-check the package
- `uv run ruff format . && uv run ruff check --fix .` — format then lint

## Coding Conventions

All public functions carry type annotations (mypy enforced, configured in `@pyproject.toml`). Use `typer` for all CLI argument and option definitions; use `rich` for all console output — never bare `print()`. Ruff is both linter and formatter; no separate config file exists yet, so defaults apply.

## Testing Guidelines

pytest. Tests go in `tests/` (to be created). Run a single test: `uv run pytest tests/test_sync.py::test_name -v`. No coverage threshold is configured yet.

## Record Classification

Every record exits the pipeline with exactly one of three outcomes: **valid** (committed to Target DB), **invalid** (written to Supabase log with the exact rule violation), or **errored** (malformed/unparseable, logged separately with an error reason). See `@context/foundation/prd.md` § Business Logic for the authoritative contract.
