# Live Sync Write Cycle (S-03) Implementation Plan

## Overview

`_connect_target`, `_write_rejections_to_supabase`, and `_commit_valid` in `dataguard/sync.py` are the last three no-op stubs in the pipeline — this plan fills them in, completing DataGuard's core hypothesis: a validation-first pipeline that turns a silent transfer failure into an auditable, recoverable event. It also wires `write_watermark()` (implemented but never called) into `run_sync`'s success path, and restructures `run_sync` to keep each extracted row paired with its classification, since both new write paths need the original field values — not just the `RecordResult` `_classify` produces.

## Current State Analysis

- `run_sync`'s orchestration already enforces the correct write order — rejections logged before Target DB commit ([dataguard/sync.py:51-64](../../../dataguard/sync.py#L51-L64)) — this plan fills stub bodies, it does not reorder anything.
- `write_watermark(ts: datetime) -> None` exists and is tested in isolation ([dataguard/watermark.py:36-38](../../../dataguard/watermark.py#L36-L38)) but has zero call sites anywhere in the repo outside its own test.
- `RecordResult` ([dataguard/models.py:12-16](../../../dataguard/models.py#L12-L16)) carries only `row_id`, `outcome`, `reason` — no original field values. `run_sync` currently discards each row's original dict once `_classify` runs (`results = [_classify(row, rules) for row in records]`, [sync.py:45-46](../../../dataguard/sync.py#L45-L46)), so nothing downstream can recover the source data.
- No Supabase client code exists anywhere in `dataguard/*.py` — `supabase` is a declared dependency ([pyproject.toml:11](../../../pyproject.toml#L11)) but never imported. `SUPABASE_URL`/`SUPABASE_KEY` are already validated by `env_check()` ([dataguard/env_check.py:6](../../../dataguard/env_check.py#L6)).
- `_connect_source` ([dataguard/sync.py:79-83](../../../dataguard/sync.py#L79-L83)) is the established pattern for `_connect_target`: `psycopg2.connect(os.environ[...])`, `OperationalError` → `RuntimeError`.
- `dataguard/cli.py` and `dataguard/watermark.py` need no changes for this slice — confirmed in `context/changes/roadmap/research.md`, which also confirmed S-03 and S-04 (watermark-override) have disjoint file footprints and can proceed in parallel.

## Desired End State

Running `dataguard sync --table orders --rules rules/orders.json` against real Source/Target DBs and a real Supabase project: invalid/errored records are written to a `rejections` table in Supabase with the exact rule violation and the original record payload; valid records are inserted into Target DB; `.watermark` advances to the current run's timestamp only after both writes succeed (or after rejection-logging alone succeeds, if there were zero valid records); any Supabase write failure aborts the run before touching Target DB.

**Verification**: `uv run pytest -m "not integration"` passes covering all three stubs and the restructured `run_sync`; `uv run mypy dataguard/` passes; a manual run against real Source/Target/Supabase instances (with `supabase/rejections.sql` applied) shows rejected rows in Supabase with `raw_record` populated, valid rows in Target DB, and `.watermark` updated.

### Key Discoveries:

- Confirmed via Context7 (`/supabase/supabase-py`): `.table(name).insert(list_of_dicts).execute()` accepts a list for a single batch insert (PostgREST natively supports array-of-objects POST bodies) — no per-row looping needed. Failures raise `postgrest.APIError` (message/code/hint/details attributes), though `create_client()` itself performs no network I/O, so connection/credential problems don't surface until `.execute()`.
- `_commit_valid` needs the same original-row problem solved as `_write_rejections_to_supabase` — it must `INSERT` real field values into Target DB, not just a `row_id`.

## What We're NOT Doing

- Not touching `dataguard/cli.py` or `dataguard/watermark.py`'s internals — confirmed unnecessary by `context/changes/roadmap/research.md`.
- Not implementing retry/backoff on Supabase write failure — abort immediately, matching every existing `_connect_*`/`_load_rules` failure path in this codebase.
- Not supporting UPSERT or per-row Target DB writes — plain batch `INSERT`, relying on the watermark (not this layer) to prevent duplicates in normal operation.
- Not auto-applying the Supabase DDL — `supabase/rejections.sql` is checked in for the engineer to run once manually, mirroring how this tool has never provisioned Source/Target DB tables either.
- Not adding a `@pytest.mark.integration` skeleton for the new stubs — unit tests with mocks only, per your explicit choice.
- Not parameterizing the Supabase table name — hardcoded `"rejections"`, with a `table_name` column distinguishing which Source/Target table each rejection came from (single Supabase project, single log, per PRD's single-table-per-invocation scope).

## Implementation Approach

Build outward from the write-order the plan already establishes: rejections first (Phase 1, including the `run_sync` row-pairing restructure both write paths depend on), then Target DB commit (Phase 2, reusing the same pairs), then watermark write-on-success (Phase 3, the final integration point). TDD throughout, mirroring `tests/test_sync.py`'s existing `_make_mock_conn` mocking style.

## Critical Implementation Details

### Row-result pairing restructure (`run_sync`)

`results = [_classify(row, rules) for row in records]` is order-preserving 1:1 against `records`, so `run_sync` must `zip(records, results)` immediately after classification and carry `(row, RecordResult)` tuples forward instead of splitting `results` alone into `invalid`/`valid` lists. `_write_rejections_to_supabase` and `_commit_valid` both take `list[tuple[dict[str, Any], RecordResult]]` instead of `list[RecordResult]`. This is the one change in this plan that isn't visible from a single file/function — it touches `run_sync`'s existing `invalid = [...]` / `valid = [...]` lines directly.

### Watermark value is wall-clock "now," not the latest record's timestamp

PRD FR-007 says "last successful run timestamp" and US-01 says "the `.watermark` file is updated to the current run timestamp" — both point at `datetime.now(timezone.utc)` captured once after writes succeed, not the max `timestamp_col` value among extracted records. Use the literal PRD wording; don't re-derive a max-extracted-timestamp scheme.

### Supabase errors surface at `.execute()`, not at client creation

`_connect_supabase()` (new helper, mirrors `_connect_source`/`_connect_target` naming) wraps `create_client(...)` in a try/except for consistency with every other `_connect_*` function, but in practice `create_client` doesn't perform I/O — the meaningful failure point, and where the "abort immediately, no retry" contract must live, is the `.execute()` call inside `_write_rejections_to_supabase`. Catch broad `Exception` there (not just `postgrest.APIError`) so a network-level failure produces the same clear abort message as an API-level one.

### Target DB connection must be explicitly closed

Unlike `_source_conn` (already closed in `run_sync`'s `finally`), no prior code path ever opened or closed a Target DB connection. `run_sync` must close `_target_conn` itself (e.g. `try/finally` around the `_commit_valid` call site), and should skip calling `_connect_target()` entirely when there are zero valid records — mirrors the existing `if invalid_pairs:` guard style around the Supabase call.

## Phase 1: Supabase rejection logging

### Overview

Restructure `run_sync` to pair rows with results, then implement `_connect_supabase` and `_write_rejections_to_supabase` against the schema decided in planning, plus ship the DDL.

### Changes Required:

#### 1. Tests (written first)

**File**: `tests/test_sync.py`

**Intent**: Cover row-shaping (row_id, table_name, outcome, reason, raw_record) sent to Supabase, the single-batch-call behavior (one `.insert()` call with the full list, not N calls), and the abort-on-failure path (mocked `.execute()` raises → `RuntimeError` with the exact "Supabase rejection write failed — aborting, Target DB untouched" message).

**Contract**: New `# --- _write_rejections_to_supabase ---` section. Add a `_make_mock_supabase_client(...)` test helper mirroring `_make_mock_conn`'s shape — a `MagicMock` whose `.table(name).insert(rows).execute()` chain is inspectable via `call_args`.

#### 2. `dataguard/sync.py` — new Supabase plumbing

**File**: `dataguard/sync.py`

**Intent**: `_connect_supabase() -> Client` creates the client from `SUPABASE_URL`/`SUPABASE_KEY`. `_write_rejections_to_supabase(invalid_pairs, table)` shapes each `(row, RecordResult)` pair into the DDL's column set and sends one batch `.insert()` call.

**Contract**:
```python
def _write_rejections_to_supabase(
    invalid_pairs: list[tuple[dict[str, Any], RecordResult]], table: str
) -> None:
    client = _connect_supabase()
    rows = [
        {
            "row_id": result.row_id,
            "table_name": table,
            "outcome": result.outcome.value,
            "reason": result.reason,
            "raw_record": row,
        }
        for row, result in invalid_pairs
    ]
    try:
        client.table("rejections").insert(rows).execute()
    except Exception as exc:
        raise RuntimeError(
            "Supabase rejection write failed — aborting, Target DB untouched"
        ) from exc
```
Import `create_client, Client` from `supabase`.

#### 3. `run_sync` restructure

**File**: `dataguard/sync.py`

**Intent**: Replace the `invalid = [...]` / `valid = [...]` split with pair-preserving equivalents (see Critical Implementation Details), and call `_write_rejections_to_supabase(invalid_pairs, table)` in place of the old `_write_rejections_to_supabase(invalid)` call.

**Contract**: `pairs = list(zip(records, results))`; `invalid_pairs = [(row, r) for row, r in pairs if r.outcome != Outcome.VALID]`; `valid_pairs = [(row, r) for row, r in pairs if r.outcome == Outcome.VALID]`. `return results` at the end is unaffected — callers of `run_sync` still get `list[RecordResult]`.

#### 4. Supabase DDL

**File**: `supabase/rejections.sql` (new)

**Intent**: Checked-in, manually-applied schema for the rejection log, matching the columns `_write_rejections_to_supabase` writes.

**Contract**:
```sql
CREATE TABLE rejections (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  row_id TEXT,
  table_name TEXT,
  outcome TEXT NOT NULL,
  reason TEXT,
  raw_record JSONB,
  synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Success Criteria:

#### Automated Verification:

- [ ] All new tests pass: `uv run pytest tests/test_sync.py -v -m "not integration"`
- [ ] Type checking passes: `uv run mypy dataguard/`
- [ ] Linting passes: `uv run ruff check . && uv run ruff format --check .`
- [ ] Full suite passes: `uv run pytest -m "not integration"`

#### Manual Verification:

- [ ] Apply `supabase/rejections.sql` in a real Supabase project's SQL editor; confirm the table is created with the expected columns.
- [ ] Run a sync against a rules file guaranteed to reject some records; confirm rows appear in Supabase's `rejections` table with `raw_record` populated and no field values leaking to console output.

---

## Phase 2: Target DB commit

### Overview

Implement `_connect_target` (mirrors `_connect_source` exactly) and `_commit_valid` as a single parameterized batch `INSERT`, reusing `valid_pairs` from Phase 1's restructure.

### Changes Required:

#### 1. Tests (written first)

**File**: `tests/test_sync.py`

**Intent**: `_connect_target` tests mirror the existing `test_connect_source_*` tests exactly (success + `OperationalError` → `RuntimeError`) against `TARGET_DB`. `_commit_valid` tests cover: correct dynamic column list derived from the row shape, `executemany` called once with all rows, `conn.commit()` called, and a no-op (cursor never touched) when `valid_pairs` is empty.

**Contract**: New `# --- _connect_target ---` and `# --- _commit_valid ---` sections, reusing `_make_mock_conn`.

#### 2. `dataguard/sync.py` — Target DB plumbing

**File**: `dataguard/sync.py`

**Intent**: `_connect_target` mirrors `_connect_source`. `_commit_valid` builds one parameterized multi-row `INSERT` from the pairs' row dicts and executes it in a single `executemany` call, then commits.

**Contract**:
```python
def _commit_valid(
    valid_pairs: list[tuple[dict[str, Any], RecordResult]],
    table: str,
    conn: psycopg2.extensions.connection,
) -> None:
    if not valid_pairs:
        return
    rows = [row for row, _ in valid_pairs]
    columns = list(rows[0].keys())
    query = sql.SQL("INSERT INTO {t} ({cols}) VALUES ({vals})").format(
        t=sql.Identifier(table),
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        vals=sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    with conn.cursor() as cur:
        cur.executemany(query, [tuple(row[c] for c in columns) for row in rows])
    conn.commit()
```

#### 3. `run_sync` wiring

**File**: `dataguard/sync.py`

**Intent**: Only connect to Target DB when there's something to write; always close the connection.

**Contract**: Replace the unconditional `_target_conn = _connect_target()` with a guard: `if valid_pairs:` open, call `_commit_valid`, close in `finally`.

### Success Criteria:

#### Automated Verification:

- [ ] All new tests pass: `uv run pytest tests/test_sync.py -v -m "not integration"`
- [ ] Type checking passes: `uv run mypy dataguard/`
- [ ] Linting passes: `uv run ruff check . && uv run ruff format --check .`
- [ ] Full suite passes: `uv run pytest -m "not integration"`

#### Manual Verification:

- [ ] Run a sync with a mix of valid/invalid records against real Source/Target DBs; confirm only valid records land in Target DB with correct field values.
- [ ] Run a sync where every record is invalid; confirm `_connect_target` is never attempted (no Target DB connection log line, no error) and the run still completes successfully.

---

## Phase 3: Watermark write-on-success

### Overview

Wire `write_watermark()` into `run_sync`'s success path, including advancing the watermark even when there were zero valid records to commit (as long as rejection-logging, if any, succeeded).

### Changes Required:

#### 1. Tests (written first)

**File**: `tests/test_sync.py`

**Intent**: Full-`run_sync` orchestration tests — the first in this codebase — patching every stub (`_connect_source`, `_extract`, `_connect_target`, `_write_rejections_to_supabase`, `_commit_valid`, `write_watermark`) to verify: watermark is written once after a successful live run; watermark is written even when `valid_pairs` is empty (all-invalid batch); watermark is NOT written in `dry_run` mode; a `_write_rejections_to_supabase` failure aborts before `_connect_target`/`_commit_valid`/`write_watermark` are ever called.

**Contract**: New `# --- run_sync ---` section using `monkeypatch.setattr("dataguard.sync.<name>", ...)` per stub, following the module-level patch style already used for `_connect_source` in `test_connect_source_success`.

#### 2. `dataguard/sync.py` — watermark wiring

**File**: `dataguard/sync.py`

**Intent**: Capture `datetime.now(timezone.utc)` once, after rejection-logging and (if any) Target DB commit both succeed, and call `write_watermark(now)` — skipped entirely in `dry_run` mode.

**Contract**: Add `from datetime import timezone` to the existing `from datetime import datetime` import; extend the `from dataguard.watermark import read_watermark` import to include `write_watermark`. Insert the `write_watermark(...)` call at the end of the `if not dry_run:` block, after the `_commit_valid` guard.

### Success Criteria:

#### Automated Verification:

- [ ] All new tests pass: `uv run pytest tests/test_sync.py -v -m "not integration"`
- [ ] Type checking passes: `uv run mypy dataguard/`
- [ ] Linting passes: `uv run ruff check . && uv run ruff format --check .`
- [ ] Full suite passes: `uv run pytest -m "not integration"`

#### Manual Verification:

- [ ] Run a live sync twice in a row against real DBs; confirm the second run's extraction starts from where the first left off (`.watermark` advanced correctly).
- [ ] Run a live sync where all records are invalid; confirm `.watermark` still advances (no infinite re-processing of the same rejected batch).
- [ ] Run with `--dry-run`; confirm `.watermark` is unchanged after the run.

---

## Testing Strategy

### Unit Tests:

- `_write_rejections_to_supabase`: row-shaping, single-batch-call, abort-on-failure (`tests/test_sync.py`).
- `_connect_target`: mirrors `_connect_source` coverage exactly.
- `_commit_valid`: dynamic column derivation, single `executemany` call, commit, empty-input no-op.
- `run_sync`: full orchestration across success, all-invalid, dry-run, and Supabase-failure-aborts-before-target-write scenarios.

### Integration Tests:

- None added automatically (per your choice) — Phase 1-3's manual verification steps cover the real end-to-end path against live Source/Target/Supabase instances.

### Manual Testing Steps:

1. Apply `supabase/rejections.sql` once in the target Supabase project.
2. Run a live sync with a mix of valid/invalid/errored records; confirm rejections in Supabase (with `raw_record`), valid rows in Target DB, and `.watermark` advanced.
3. Run again immediately; confirm no records are re-processed (watermark correctly gates re-extraction).
4. Run with `--dry-run`; confirm no writes anywhere and `.watermark` unchanged.
5. Confirm no raw field values ever appear in console output — only in the Supabase rejection log, per the AGENTS.md hard rule.

## Performance Considerations

Both write paths are single batch calls (one `executemany` for Target DB, one `.insert([...])` for Supabase) rather than per-record round-trips — required to have a realistic chance at the PRD's 10,000-records/60s NFR, and confirmed as the recommended approach in planning.

## Migration Notes

`supabase/rejections.sql` is new schema, not a migration of existing data — no prior rejection log existed.

## References

- Research: `context/changes/roadmap/research.md` (S-03/S-04 parallelizability assessment — this plan's scope is entirely consistent with its findings)
- `dataguard/sync.py:51-64` — existing write-order orchestration this plan fills in
- `dataguard/watermark.py:36-38` — `write_watermark`, unused until this plan
- `dataguard/models.py:12-16` — `RecordResult`, unchanged by this plan
- `context/foundation/prd.md` § Business Logic — write-order hard rule; FR-007/US-01 — watermark semantics
- Supabase Python client docs (via Context7, `/supabase/supabase-py`) — batch insert and `postgrest.APIError` behavior

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Supabase rejection logging

#### Automated

- [ ] 1.1 All new tests pass: `uv run pytest tests/test_sync.py -v -m "not integration"`
- [ ] 1.2 Type checking passes: `uv run mypy dataguard/`
- [ ] 1.3 Linting passes: `uv run ruff check . && uv run ruff format --check .`
- [ ] 1.4 Full suite passes: `uv run pytest -m "not integration"`

#### Manual

- [ ] 1.5 `supabase/rejections.sql` applied and table shape confirmed in a real Supabase project
- [ ] 1.6 Rejected records appear in Supabase with `raw_record` populated, no console leakage

### Phase 2: Target DB commit

#### Automated

- [ ] 2.1 All new tests pass: `uv run pytest tests/test_sync.py -v -m "not integration"`
- [ ] 2.2 Type checking passes: `uv run mypy dataguard/`
- [ ] 2.3 Linting passes: `uv run ruff check . && uv run ruff format --check .`
- [ ] 2.4 Full suite passes: `uv run pytest -m "not integration"`

#### Manual

- [ ] 2.5 Only valid records land in Target DB with correct field values
- [ ] 2.6 All-invalid batch completes without ever attempting a Target DB connection

### Phase 3: Watermark write-on-success

#### Automated

- [ ] 3.1 All new tests pass: `uv run pytest tests/test_sync.py -v -m "not integration"`
- [ ] 3.2 Type checking passes: `uv run mypy dataguard/`
- [ ] 3.3 Linting passes: `uv run ruff check . && uv run ruff format --check .`
- [ ] 3.4 Full suite passes: `uv run pytest -m "not integration"`

#### Manual

- [ ] 3.5 Second consecutive run only processes records created after the first run
- [ ] 3.6 All-invalid batch still advances the watermark
- [ ] 3.7 `--dry-run` leaves `.watermark` unchanged
