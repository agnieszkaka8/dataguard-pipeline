# Source DB Extraction Implementation Plan

## Overview

Implement the source database connectivity and incremental record extraction layer for DataGuard's S-01 slice. Introduces `dataguard/watermark.py` (read/write `.watermark` file), implements `_connect_source()` and `_extract()` in `sync.py`, adds a `--timestamp-col` CLI option, and establishes the `tests/` directory with unit coverage.

## Current State Analysis

- `dataguard/sync.py:60-62` — `_connect_source()` raises `NotImplementedError`
- `dataguard/sync.py:68-69` — `_extract()` returns `[]` unconditionally; takes `since: str | None` but ignores it
- No watermark module exists; `.watermark` is referenced in PRD FR-007 but no code reads or writes it
- `dataguard/cli.py:23-30` — `sync` command wired with `--table`, `--rules`, `--dry-run`, `--since`; no `--timestamp-col` yet
- `env_check.py` guarantees `SOURCE_DB` is set before `run_sync` is called — safe to use `os.environ["SOURCE_DB"]` in the connection stub
- `psycopg2-binary>=2.9.12` is installed; `python-dotenv>=1.2.2` is installed

## Desired End State

`uv run dataguard sync --dry-run --table <name> --rules rules/orders.json` completes without error: connects to Source DB, reads (or initialises) the watermark, extracts records since that watermark using the `created_at` column (or `--timestamp-col` override), prints the record count, and exits cleanly. The `.watermark` file is written only after a full successful non-dry-run — that final wire-up is S-03's responsibility.

### Key Discoveries

- `run_sync()` already calls `_connect_source()` and `_extract(table, since)` — both need their signatures extended but no orchestrator restructuring
- `_source_conn` is assigned but never passed to `_extract` in the current scaffold — `_extract` needs the connection added to its signature
- `cli.py` top-level `except Exception` already converts any exception to `Exit(code=1)` — `_connect_source` just needs a safe message before propagating
- Table and column names must go through `psycopg2.sql.Identifier` — the default f-string pattern is a SQL injection vector

## What We're NOT Doing

- Implementing `_connect_target`, `_classify`, `_write_rejections_to_supabase`, or `_commit_valid` — those are S-02/S-03
- Writing the watermark at end of run — belongs to S-03 (after `_commit_valid` succeeds)
- Adding `--full-table` or any CLI flag beyond `--timestamp-col`
- Setting up a real test database — integration tests are skipped by default

## Implementation Approach

Four additive changes to existing stubs, plus one new module:

1. New `dataguard/watermark.py` handles `.watermark` file lifecycle (read / write / first-run)
2. `_connect_source()` wraps `psycopg2.connect(os.environ["SOURCE_DB"])` with safe error handling
3. `_extract()` executes a single parameterised `SELECT … WHERE ts_col > %s` using `psycopg2.sql` identifiers
4. `cli.py` gains `--timestamp-col`; `run_sync` gains `timestamp_col` and integrates `read_watermark`

## Critical Implementation Details

**SQL identifier safety** — table name and timestamp column are user-supplied strings. Use `psycopg2.sql.Identifier`, not f-string interpolation:
```python
from psycopg2 import sql
q = sql.SQL("SELECT * FROM {t} WHERE {c} > %s ORDER BY {c}").format(
    t=sql.Identifier(table), c=sql.Identifier(timestamp_col)
)
```

**Undefined column detection** — `psycopg2.errors.UndefinedColumn` (a `ProgrammingError` subclass) is raised when the timestamp column doesn't exist. Catch it specifically and re-raise as `RuntimeError(f"Column '{timestamp_col}' not found in table '{table}'")`. Column name is safe to surface; it contains no credentials.

---

## Phase 1: Watermark module

### Overview

Introduce `dataguard/watermark.py` with two public functions. No changes to existing files in this phase.

### Changes Required

#### 1. New watermark module

**File**: `dataguard/watermark.py`

**Intent**: Provide `read_watermark(since)` and `write_watermark(ts)` so the rest of the codebase never touches the `.watermark` file directly.

**Contract**:

`read_watermark(since: str | None) -> datetime | None`:
- If `since` is not `None`: parse as ISO 8601, return a UTC-aware `datetime`; let `ValueError` propagate if unparseable (bad user input)
- If `.watermark` exists: parse as ISO 8601, return UTC-aware `datetime`; if unreadable or unparseable raise `RuntimeError("Corrupted .watermark file — delete it to start fresh")`
- If `.watermark` is absent: `console.print("[yellow]No .watermark found — processing all records (first run)[/yellow]")` and return `None`

`write_watermark(ts: datetime) -> None`:
- Write `ts.isoformat()` as the sole line in `.watermark`

Use a module-level `console = Console()` consistent with the rest of the package. Both functions operate on `Path(".watermark")` relative to the working directory (where the CLI is invoked).

### Success Criteria

#### Automated Verification

- Type check: `uv run mypy dataguard/watermark.py`
- Lint: `uv run ruff check dataguard/watermark.py`

---

## Phase 2: Connection, extraction, and CLI wiring

### Overview

Implement `_connect_source()` and `_extract()`, update `run_sync()` to integrate watermark reading and pass the connection to `_extract`, add `--timestamp-col` to the CLI.

### Changes Required

#### 1. Implement `_connect_source`

**File**: `dataguard/sync.py`

**Intent**: Open a psycopg2 connection to Source DB using the `SOURCE_DB` DSN; translate connection failures into a safe, credential-free error message.

**Contract**: Return type changes from `object` to `psycopg2.extensions.connection`. Import `psycopg2` at the top of the function (or module). Catch `psycopg2.OperationalError`, re-raise as `RuntimeError("Source DB connection failed")` — do not include the DSN or any connection parameter in the message.

#### 2. Implement `_extract`

**File**: `dataguard/sync.py`

**Intent**: Query all rows from `table` where `timestamp_col > since_dt`, ordered by `timestamp_col`, and return them as `list[dict]`. When `since_dt` is `None` (first run), return all rows ordered by `timestamp_col`.

**Contract**: Signature changes to:
```python
def _extract(
    table: str,
    since_dt: datetime | None,
    timestamp_col: str,
    conn: psycopg2.extensions.connection,
) -> list[dict]:
```
Build the query using `psycopg2.sql.Identifier` (see Critical Implementation Details). Use `cursor.description` to derive column names and `zip` them with each fetched tuple to produce dicts. Catch `psycopg2.errors.UndefinedColumn` and re-raise as `RuntimeError(f"Column '{timestamp_col}' not found in table '{table}'"`)`.

#### 3. Update `run_sync` signature and watermark integration

**File**: `dataguard/sync.py`

**Intent**: Add `timestamp_col` parameter, resolve `since_dt` via `read_watermark`, and thread both into `_extract`.

**Contract**: Add `timestamp_col: str = "created_at"` as the last keyword argument to `run_sync`. Import `read_watermark` from `dataguard.watermark`. Replace the current `records = _extract(table, since)` call with:
```python
since_dt = read_watermark(since)
records = _extract(table, since_dt, timestamp_col, _source_conn)
```

#### 4. Add `--timestamp-col` CLI option

**File**: `dataguard/cli.py`

**Intent**: Expose `--timestamp-col` so engineers can specify a non-default timestamp column without editing any config file.

**Contract**: Add to the `sync` command:
```python
timestamp_col: str = typer.Option(
    "created_at", "--timestamp-col", help="Timestamp column used for incremental extraction"
)
```
Pass `timestamp_col=timestamp_col` to `run_sync`.

### Success Criteria

#### Automated Verification

- Full type check: `uv run mypy dataguard/`
- Full lint: `uv run ruff check .`

#### Manual Verification

- `uv run dataguard sync --dry-run --table <name> --rules rules/orders.json` with a valid `SOURCE_DB` in `.env`: prints "Connecting…", "Extracting…", record count, "Dry-run mode", and summary table — no traceback
- First run (no `.watermark`): first-run warning appears before "Extracting…"
- `uv run dataguard sync --dry-run ...` with an unreachable host in `SOURCE_DB`: prints "Sync failed: RuntimeError", exits non-zero, no host/port/credentials in output

**Implementation Note**: After this phase passes automated checks, pause for manual confirmation before proceeding to Phase 3.

---

## Phase 3: Tests

### Overview

Create `tests/` directory and add unit tests for all S-01 branches. Add an integration test skeleton marked `pytest.mark.integration` (skipped in CI by default).

### Changes Required

#### 1. Test infrastructure

**Files**: `tests/__init__.py` (empty), `tests/conftest.py`

**Intent**: Bootstrap the pytest package and register the `integration` marker so `uv run pytest` skips real-DB tests by default.

**Contract**: `conftest.py` adds:
```python
def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires a live database")
```
Pass `-m "not integration"` in CI or as the default; integration tests run via `uv run pytest -m integration`.

#### 2. Watermark unit tests

**File**: `tests/test_watermark.py`

**Intent**: Cover all branches of `read_watermark` and `write_watermark` without filesystem side-effects.

**Contract**: Use `pytest`'s `tmp_path` fixture and `monkeypatch` to redirect `Path(".watermark")` to a temp file. Test cases:
- Missing file → returns `None`, first-run message printed
- Valid file → returns correct UTC-aware `datetime`
- Corrupted file → raises `RuntimeError`
- `since` provided → returns parsed datetime regardless of file state
- `write_watermark` → file content equals `ts.isoformat()`

#### 3. Sync unit tests

**File**: `tests/test_sync.py`

**Intent**: Cover `_connect_source` error path and all `_extract` branches using `unittest.mock.patch`.

**Contract**: Patch `psycopg2.connect`. Test cases:
- `_connect_source()` with successful mock → returns connection
- `_connect_source()` with `OperationalError` → raises `RuntimeError("Source DB connection failed")`
- `_extract(table, since_dt=None, ...)` → builds query without `WHERE`, returns all mocked rows as dicts
- `_extract(table, since_dt=datetime(...), ...)` → query includes `WHERE ts_col > %s`
- `_extract` when cursor raises `UndefinedColumn` → raises `RuntimeError` naming the column
- `@pytest.mark.integration` skeleton: connects to real `SOURCE_DB`, calls `_extract`; skipped by default

### Success Criteria

#### Automated Verification

- All unit tests pass: `uv run pytest tests/ -v -m "not integration"`
- Type check: `uv run mypy dataguard/`
- Lint + format: `uv run ruff check . && uv run ruff format --check .`

---

## Testing Strategy

### Unit Tests
- `tests/test_watermark.py` — 5 cases, all `read_watermark` / `write_watermark` branches
- `tests/test_sync.py` — 5 unit cases + 1 integration skeleton

### Integration Tests
- `@pytest.mark.integration` in `tests/test_sync.py`; require real `SOURCE_DB`; skipped by default

### Manual Testing Steps
1. With a valid `SOURCE_DB` in `.env`, run `uv run dataguard sync --dry-run --table <table> --rules rules/orders.json` — verify connection + extraction + summary
2. Delete `.watermark` and rerun — verify first-run warning
3. Set `SOURCE_DB` to an unreachable host — verify exit 1 and no credentials in output

## References

- Roadmap S-01: `context/foundation/roadmap.md` § S-01
- PRD requirements: `context/foundation/prd.md` § FR-001, FR-003, FR-004, FR-007
- psycopg2 sql composition: https://www.psycopg.org/docs/sql.html

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Watermark module

#### Automated

- [x] 1.1 Type check passes: `uv run mypy dataguard/watermark.py` — 0190a0e
- [x] 1.2 Lint passes: `uv run ruff check dataguard/watermark.py` — 0190a0e

### Phase 2: Connection, extraction, and CLI wiring

#### Automated

- [x] 2.1 Full type check: `uv run mypy dataguard/`
- [x] 2.2 Full lint: `uv run ruff check .`

#### Manual

- [ ] 2.3 Dry-run with valid SOURCE_DB: connects, extracts, prints summary — no traceback
- [ ] 2.4 First run (no .watermark): first-run warning visible before extraction
- [x] 2.5 Unreachable SOURCE_DB: "Sync failed: RuntimeError", exit non-zero, no credentials in output

### Phase 3: Tests

#### Automated

- [ ] 3.1 All unit tests pass: `uv run pytest tests/ -v -m "not integration"`
- [ ] 3.2 Type check: `uv run mypy dataguard/`
- [ ] 3.3 Lint + format check: `uv run ruff check . && uv run ruff format --check .`
