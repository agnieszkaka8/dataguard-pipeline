# Source DB Extraction — Plan Brief

> Full plan: `context/changes/source-db-extraction/plan.md`

## What & Why

Connect DataGuard to a PostgreSQL source database and extract records incrementally using a `.watermark` file. Without this slice, the pipeline has no data to validate or sync — it is the foundational input to every downstream slice (S-02, S-03, S-04).

## Starting Point

`dataguard/sync.py` has `_connect_source()` (raises `NotImplementedError`) and `_extract()` (returns `[]`). No watermark code exists anywhere. `psycopg2-binary` is installed and `SOURCE_DB` is validated by `env_check.py` before `run_sync` is ever reached.

## Desired End State

`uv run dataguard sync --dry-run --table <name> --rules rules/orders.json` connects to Source DB, reads (or initialises) the `.watermark` file, extracts records since that watermark, and prints a count — all without error. A new `dataguard/watermark.py` module owns `.watermark` lifecycle. Unit tests cover all extraction branches without a real database.

## Key Decisions Made

| Decision | Choice | Why | Source |
|---|---|---|---|
| Timestamp column | `created_at` default + `--timestamp-col` CLI override | Works for common case; typer wiring already in place | Plan |
| No timestamp column | Hard error (`RuntimeError`) | Matches PRD exit-non-zero rule; prevents silent full-table re-runs | Plan |
| Fetch strategy | Single `SELECT`, `fetchall()` | Simplest correct approach for ≤10k rows; no streaming overhead needed | Plan |
| Watermark format | ISO 8601 UTC string | Human-readable; usable directly as `--since` value | Plan |
| Row identity | `row['id']` → fallback to index | Sufficient for S-01; rejection log references not needed until S-03 | Plan |
| Connection error handling | Catch `OperationalError` → `RuntimeError` with safe message | Prevents psycopg2 from leaking DSN in error output | Plan |
| Testing | Unit + psycopg2 mock + integration skeleton | Fast CI; real-DB opt-in via `pytest.mark.integration` | Plan |

## Scope

**In scope:** `dataguard/watermark.py`, `_connect_source()`, `_extract()`, `run_sync()` signature update, `--timestamp-col` CLI option, `tests/test_watermark.py`, `tests/test_sync.py`, `tests/conftest.py`

**Out of scope:** `_connect_target`, `_classify`, `_write_rejections_to_supabase`, `_commit_valid`, watermark write-back (S-03), `--full-table` flag

## Architecture / Approach

Additive changes to existing stubs — one new module (`watermark.py`), no new frameworks. `read_watermark(since)` resolves a `datetime | None` consumed by `_extract`. Table and column names go through `psycopg2.sql.Identifier` (SQL injection prevention — the single non-obvious constraint). Full call chain: `cli.py` → `run_sync()` → `read_watermark()` + `_connect_source()` + `_extract()`.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Watermark module | `dataguard/watermark.py` with read / write / first-run logic | Timezone handling — must always normalise to UTC |
| 2. Connection + extraction | Live `_connect_source`, `_extract`, `--timestamp-col` wired | SQL injection if `psycopg2.sql.Identifier` is skipped |
| 3. Tests | Unit tests for all branches + integration skeleton | Mock may diverge from real psycopg2 — acceptable at this stage |

**Prerequisites:** Valid `SOURCE_DB` in `.env` for Phase 2 manual verification only. No other slices must be complete.
**Estimated effort:** ~1 session across 3 phases.

## Open Risks & Assumptions

- `SOURCE_DB` is assumed to be a standard PostgreSQL DSN (`postgresql://user:pass@host/db`) — psycopg2 format; any other format surfaces at connection time
- `created_at` is assumed to be a `timestamptz` column; a naive `timestamp` column may cause timezone comparison drift

## Success Criteria (Summary)

- `uv run dataguard sync --dry-run` connects, extracts, and exits cleanly with a valid `SOURCE_DB`
- All unit tests pass without a real database: `uv run pytest -m "not integration"`
- No credentials or host/port appear in error output when `SOURCE_DB` is unreachable
