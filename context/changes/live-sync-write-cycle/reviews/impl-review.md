<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Live Sync Write Cycle (S-03)

- **Plan**: context/changes/live-sync-write-cycle/plan.md
- **Scope**: All phases (1–3 of 3)
- **Date**: 2026-08-22
- **Verdict**: REJECTED
- **Findings**: 1 critical 3 warnings 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | FAIL |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | WARNING |

## Findings

### F1 — `raw_record` breaks JSON serialization on any timestamp/Decimal/UUID field

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: dataguard/sync.py:161 (body 151-170)
- **Detail**: `_write_rejections_to_supabase` embeds the raw source row verbatim into `raw_record`, then sends it via `client.table("rejections").insert(rows).execute()`. `supabase-py`'s transport (`postgrest` → `httpx`) serializes the request body with stdlib `json.dumps` and no `default=` handler (`httpx/_content.py:5,177`). Verified directly: `json.dumps({"created_at": datetime.now()})` raises `TypeError: Object of type datetime is not JSON serializable`. `psycopg2` returns native `datetime` objects for timestamp columns, and `timestamp_col` defaults to `"created_at"` — so any live run with a rejected record on a table with a timestamp column (the common case, not an edge case) will raise inside the try block, get caught by `except Exception`, and abort the entire run with `RuntimeError("Supabase rejection write failed...")`. The feature is effectively non-functional against realistic schemas. No test in `tests/test_sync.py` uses a `datetime`/`Decimal`/`UUID` field in `invalid_pairs`, so this was never caught.
- **Fix**: JSON-sanitize each row before building the insert payload — `"raw_record": json.loads(json.dumps(row, default=str))` or a small `_json_safe(row)` helper — and add a test with a `datetime` field in the row to lock this in.
  - Strength: One-line change at the exact point the row dict is shaped into the Supabase payload; `default=str` handles datetime/Decimal/UUID uniformly without per-type branching.
  - Tradeoff: `default=str` loses type fidelity in the stored JSONB (a Decimal becomes a string, not a number) — acceptable here since `raw_record` is an audit/debug payload, not queried numerically.
  - Confidence: HIGH — reproduced the failure directly against this repo's dependencies (httpx's actual serialization path), not a hypothetical.
  - Blind spot: Haven't checked whether any Source DB column type beyond datetime/Decimal/UUID (e.g. `bytes` from a `bytea` column) would also fail `default=str` silently-wrong rather than erroring — worth a quick scan of the real Source DB schema before closing this out.
- **Decision**: PENDING

### F2 — `_commit_valid` has no error boundary around `executemany`/`commit`

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: dataguard/sync.py:187-189
- **Detail**: Unlike `_connect_source`, `_connect_target`, and `_write_rejections_to_supabase` (all catch and re-raise as `RuntimeError` with a clear message), `_commit_valid`'s `cur.executemany(...)` / `conn.commit()` calls are unguarded — a raw psycopg2 exception (constraint violation, type mismatch, dropped connection) propagates unwrapped. `run_sync`'s `finally` still closes the connection and no leakage occurs, but it's an inconsistent boundary and untested (existing `_commit_valid` tests cover only success and empty-list).
- **Fix**: Wrap in try/except → `raise RuntimeError("Target DB write failed") from exc`; add `test_commit_valid_raises_on_db_error`.
- **Decision**: PENDING

### F3 — `_connect_supabase` catches bare `Exception`, masking missing-env-var errors

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: dataguard/sync.py:144-148
- **Detail**: Sibling connectors (`_connect_source`, `_connect_target`) catch the specific `psycopg2.OperationalError`; `_connect_supabase` catches bare `Exception`, and since the `os.environ[...]` lookups sit inside the try, a missing `SUPABASE_URL`/`SUPABASE_KEY` raises `KeyError`, masked as "Supabase client creation failed" instead of a config error. Currently moot in the CLI path (`env_check()` validates all four required vars first), but inconsistent and would misreport root cause if this function is ever called outside that guard.
- **Fix**: Narrow the except to what `create_client` actually documents raising, or validate/read the two env vars explicitly before entering the try.
- **Decision**: PENDING

### F4 — Write-order invariant not directly asserted in the mixed-batch success test

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: tests/test_sync.py:363-384 (`test_run_sync_writes_watermark_once_on_success`)
- **Detail**: This is the only orchestration test with a genuinely mixed batch (one valid, one invalid) — exactly the scenario AGENTS.md's write-order hard rule targets — but it never asserts *ordering* between `_write_rejections_to_supabase` and `_commit_valid` (both mocks are only checked for call count / close). The abort-path test proves ordering indirectly for the failure case only; the success-path ordering invariant is asserted nowhere directly.
- **Fix**: Attach both mocks to a shared `unittest.mock.Mock()` manager and assert `mock_calls` order in the mixed-batch success test.
- **Decision**: PENDING

### F5 — Stale "Stubs" section comment

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: dataguard/sync.py:81-83
- **Detail**: The comment `"Stubs — each becomes its own module/function as implementation grows"` is stale — `_connect_supabase`, `_connect_target`, `_write_rejections_to_supabase`, `_commit_valid` are now full implementations, not stubs.
- **Fix**: Update or remove the comment.
- **Decision**: PENDING

### F6 — Minor test-helper redundancy

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tests/test_sync.py:226-228, 324-326
- **Detail**: `_make_mock_supabase_client()` is a one-line pass-through around `MagicMock()` with no setup, adding indirection without value. `test_commit_valid_dynamic_columns_and_executemany` re-derives `conn.cursor.return_value.__enter__.return_value` inline instead of extending `_make_mock_conn` — mirrors an existing precedent elsewhere in the file, so not a new deviation, just a consolidation opportunity.
- **Fix**: Optional — collapse into one `_mock_conn_with_cursor()` helper shared by extract/commit tests; drop `_make_mock_supabase_client` in favor of `MagicMock()` directly.
- **Decision**: PENDING

### F7 — `rejections.sql` schema minor hardening opportunities

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: supabase/rejections.sql
- **Detail**: `row_id TEXT` will receive integer values for integer/bigint-PK tables (`RecordResult.row_id` is `Any`, typically `int`); PostgREST generally coerces JSON scalars to the column's text representation without error, so unlikely to break in practice. No `outcome` CHECK constraint restricting to `('invalid','errored')`, no RLS statement (acceptable if this table is only ever written with the service-role key).
- **Fix**: None required; optional hardening (`CHECK` constraint, explicit RLS comment) if desired.
- **Decision**: PENDING

## Compliant / no issue found

- Write order (`dataguard/sync.py:54-69`): rejections are written before Target DB is touched; `_connect_target`/`_commit_valid` only run when `valid_pairs` is non-empty; a `_write_rejections_to_supabase` failure propagates before `_connect_target` is ever called (verified structurally and by `test_run_sync_aborts_before_target_and_watermark_on_supabase_failure`).
- Connection cleanup (`sync.py:74-78`): both `_source_conn` and `_target_conn` are closed in `finally` regardless of exception path.
- SQL injection: table/column identifiers in `_extract` and `_commit_valid` go through `psycopg2.sql.Identifier`, safe regardless of origin.
- No credential/raw-value leakage: no `console.print` in `dataguard/sync.py` prints row/record content or connection strings.
- `console = Console(no_color=bool(os.environ.get("NO_COLOR")))` matches the project-wide convention (per [[always-pass-no-color-to-rich-console]] lesson).
- Plan adherence: all 9 planned Phase 1-3 items verified MATCH against the plan's exact contracts; no scope creep against the "What We're NOT Doing" list; diff footprint exactly `dataguard/sync.py`, `tests/test_sync.py`, `supabase/rejections.sql` as planned.
- All automated success criteria re-verified passing at review time: 73 tests, mypy clean, ruff clean.
