# Testing Critical Path Regression Guard Implementation Plan

## Overview

Lock the two AGENTS.md hard rules — write order (Supabase-before-Target-DB,
abort-on-Supabase-failure) and no-leakage (no credential or raw field value
in console output, error messages, or local logs) — with dedicated, named
regression tests. This is `context/foundation/test-plan.md` §3 Phase 2,
covering Risk #2 and Risk #3 from the risk map. Purely additive test work;
no production code changes.

## Current State Analysis

`dataguard/sync.py`'s `run_sync` already implements the correct write order
and abort behavior — this was correct from inception (per
`context/archive/2026-07-24-live-sync-write-cycle/plan.md`), never
regressed. The gap is coverage, not behavior:

- **Write order**: `tests/test_sync.py::test_run_sync_writes_watermark_once_on_success`
  (`tests/test_sync.py:396-428`) already asserts call order via a
  `Mock()` manager with `attach_mock`, but the assertion is incidental —
  buried inside a test named for watermark-write behavior. Nothing in the
  suite is named for, or solely responsible for, the write-order guarantee.
  `test_run_sync_aborts_before_target_and_watermark_on_supabase_failure`
  (`tests/test_sync.py:481-511`) already correctly proves the abort branch
  (no Target DB connection opened, no watermark written, source closed) but
  lives mixed among ~30 other unrelated test functions in the same file.
- **No-leakage**: zero coverage exists. `dataguard/cli.py:54`
  (`console.print(f"[red]Sync failed:[/red] {type(exc).__name__}")`) and
  `dataguard/env_check.py` already do the right thing today (type name
  only, variable names only), but nothing guards that from regressing —
  a future contributor adding a "helpful" `str(exc)` anywhere in
  `cli.py`/`sync.py`/`watermark.py` would leak silently and no test would
  fail.

### Key Discoveries:

- `dataguard/sync.py:54-66` — the actual write-order orchestration block
  under test (rejections logged at line 59 before `_commit_valid` at
  line 66).
- `dataguard/cli.py:45-55` — the only place exceptions become user-visible
  output; it already uses `type(exc).__name__`, never `str(exc)`. This is
  the boundary the no-leakage tests must exercise end-to-end, not just at
  the `sync.py`/`watermark.py` function level.
- `dataguard/cli.py`'s `sync()` command is a plain function decorated with
  `@app.command()` — it can be called directly in tests (no
  `typer.testing.CliRunner` needed, consistent with test-plan §4 which
  reserves `CliRunner` for Phase 4), but every parameter must be passed
  explicitly by keyword — the `typer.Option(...)` objects are the real
  Python defaults, so omitting an argument passes an `OptionInfo` object
  instead of a usable value.
- `tests/test_cli.py:25` and `tests/test_sync.py` both use
  `patch("<module>.console.print")` (mock-based capture) rather than
  `capsys`/`capfd`, since each module (`cli.py`, `sync.py`, `watermark.py`)
  instantiates its own `Console()` — this plan follows that existing
  convention for the no-leakage harness rather than introducing stdout
  capture.
- No `logging` module usage anywhere in `dataguard/` — Rich `console.print`
  and raised exception messages are the only two leak surfaces.

## Desired End State

Two new test files exist, each independently provable as a real regression
guard (not just a passing test): temporarily reordering the write blocks
in `run_sync`, or temporarily adding a `str(exc)`-style leak anywhere in
`cli.py`/`sync.py`/`watermark.py`, causes the corresponding new test to
fail with a clear, attributable message. `test_sync.py`'s incidental
write-order assertion is removed, leaving the new file as sole owner of
that hard rule's proof.

Verify via: `uv run pytest tests/test_write_order_regression.py
tests/test_no_leakage_regression.py -v`, and the full suite
(`uv run pytest -m "not integration"`), `uv run mypy dataguard/`, and
`uv run ruff format . && uv run ruff check .` all still pass.

## What We're NOT Doing

- No production code changes — `run_sync`, `cli.py`, and `watermark.py`
  are already correct; this phase adds coverage only.
- No `typer.testing.CliRunner` — reserved for test-plan §3 Phase 4
  (Source DB & watermark integration coverage).
- No real Postgres/Supabase instance — this phase stays at the unit/
  contract layer per test-plan §2's Risk Response Guidance for Risk #2
  and Risk #3; Risk #1 (realistic Supabase payload shapes) is Phase 3.
- No change to `env_check.py` — it already satisfies the no-leakage rule
  correctly and isn't a regression risk (it never had record-value or
  connection-string access to leak in the first place).
- No new CI wiring — both new test files run automatically under the
  existing required `pytest` gate from test-plan §3 Phase 1.

## Implementation Approach

Two independent phases, one per risk, each self-contained in its own new
file so the file name itself signals which hard rule it guards (matches
the discoverability gap that let Risk #2 go under-tested and Risk #3 go
completely uncovered). Phase 1 first (extends an existing pattern), then
Phase 2 (net-new pattern, no prior art in the suite).

## Critical Implementation Details

### Debug & observability

Both phases require proving the new test is a real regression guard, not
a tautology that would pass regardless of the code it's supposed to
protect. For Phase 1, temporarily swap the order of the
`_write_rejections_to_supabase` / `_commit_valid` calls inside `run_sync`
locally and confirm `tests/test_write_order_regression.py` fails with a
clear message identifying the violated order — then revert the swap
before committing. For Phase 2, temporarily change
`dataguard/cli.py:54` to interpolate `str(exc)` instead of
`type(exc).__name__` and confirm `tests/test_no_leakage_regression.py`
fails — then revert. Both are manual verification steps (see each phase's
Manual Verification), not something to leave in the codebase.

## Phase 1: Write-order regression guard

### Overview

Give the write-order hard rule (Risk #2) a single, dedicated, named home
that fails specifically and attributably if a future change reorders or
weakens the guarantee.

### Changes Required:

#### 1. New dedicated write-order test file

**File**: `tests/test_write_order_regression.py`

**Intent**: Prove, in one file owned solely by this hard rule, that (a) on
a run with both invalid and valid records, `_write_rejections_to_supabase`
is called before `_commit_valid`, and (b) when the Supabase write raises,
no Target DB connection is ever opened, `_commit_valid` is never called,
and the watermark is never written. Part (b) is the existing
`test_run_sync_aborts_before_target_and_watermark_on_supabase_failure`
relocated here — its logic already fully satisfies Risk Response
Guidance row #2's "what would prove protection" for the abort branch, it
just needs to live under this hard rule's dedicated ownership rather than
mixed into `test_sync.py`'s general `run_sync` test group.

**Contract**: Mirrors the existing `run_sync` test setup in
`tests/test_sync.py` (`monkeypatch.setattr` on `dataguard.sync._connect_source`,
`_extract`, `_connect_target`, `_write_rejections_to_supabase`,
`_commit_valid`, `write_watermark`). The order-proof test uses a `Mock()`
manager with `attach_mock` on `_write_rejections_to_supabase` and
`_commit_valid`, asserting `[c[0] for c in manager.mock_calls] ==
["write_rejections", "commit_valid"]` against the real (unmocked)
`run_sync` orchestration — this is what makes it fail on an actual
reordering, not just on a broken mock.

#### 2. Remove the incidental assertion from `test_sync.py`

**File**: `tests/test_sync.py`

**Intent**: `test_run_sync_writes_watermark_once_on_success` keeps its
watermark-focused assertions (`write_watermark_mock.assert_called_once()`,
`source_conn.close.assert_called_once()`, `target_conn.close.assert_called_once()`)
but drops the `manager`/`attach_mock` scaffolding and the
`mock_calls`-order assertion — that proof now lives solely in
`test_write_order_regression.py`.

**Contract**: Remove the `manager = Mock()` / `attach_mock` lines and the
order-assertion block; the test function keeps its current name and
remaining assertions unchanged.

### Success Criteria:

#### Automated Verification:

- New file passes: `uv run pytest tests/test_write_order_regression.py -v`
- Full suite still passes: `uv run pytest -m "not integration"`
- Type checking passes: `uv run mypy dataguard/`
- Lint and format clean: `uv run ruff format --check . && uv run ruff check .`

#### Manual Verification:

- Temporarily swap the `_write_rejections_to_supabase` / `_commit_valid`
  call order in `dataguard/sync.py`'s `run_sync`; confirm
  `test_write_order_regression.py` fails with a clear order-violation
  message; revert the swap.
- Confirm `tests/test_sync.py`'s `test_run_sync_writes_watermark_once_on_success`
  no longer references `manager`/`attach_mock` (diff review).

---

## Phase 2: No-leakage regression guard

### Overview

Give the no-leakage hard rule (Risk #3) its first coverage, using a
sentinel-based sweep that catches any future leak anywhere in
`cli.py`/`sync.py`/`watermark.py`, not just today's known-safe call
sites.

### Changes Required:

#### 1. New dedicated no-leakage test file

**File**: `tests/test_no_leakage_regression.py`

**Intent**: Prove that a distinctive sentinel value — injected via fake
`SOURCE_DB`/`TARGET_DB`/`SUPABASE_URL`/`SUPABASE_KEY` env vars and via a
record field — never appears in any `console.print` call across
`cli.py`, `sync.py`, `watermark.py`, nor in any raised exception's
`str()`, across: the success path, dry-run, and every failure branch
(source connect failure, target connect failure, rules-load failure,
Supabase connect failure, Supabase write failure, Target DB write
failure, corrupted-watermark failure, risky `--since` warning). Failure
branches are triggered by making the raising side (`psycopg2.connect`,
the Supabase client, `_commit_valid`'s cursor) raise an exception whose
own message embeds the sentinel — matching realistic driver behavior,
where connection/constraint errors often echo back the offending
DSN/value.

**Contract**: A shared per-test harness patches `console.print` on all
three modules' `Console` instances (`dataguard.cli.console`,
`dataguard.sync.console`, `dataguard.watermark.console`) into one
captured-calls list, then invokes `dataguard.cli.sync(...)` directly with
explicit keyword arguments for every parameter (see Key Discoveries —
`typer.Option` defaults require this) inside `pytest.raises(typer.Exit)`
for failure scenarios. After each scenario, assert the sentinel string is
absent from every captured `console.print` argument and from
`str(excinfo.value)` plus `str(excinfo.value.__cause__)` where a cause
chain exists.

### Success Criteria:

#### Automated Verification:

- New file passes: `uv run pytest tests/test_no_leakage_regression.py -v`
- Full suite still passes: `uv run pytest -m "not integration"`
- Type checking passes: `uv run mypy dataguard/`
- Lint and format clean: `uv run ruff format --check . && uv run ruff check .`

#### Manual Verification:

- Temporarily change `dataguard/cli.py:54` to interpolate `str(exc)`
  instead of `type(exc).__name__`; confirm
  `test_no_leakage_regression.py` fails, identifying the leak; revert.
- Confirm every console.print call site listed in `context/foundation/test-plan.md`
  §2 Risk Response Guidance row #3 (`cli.py`, `sync.py`, `watermark.py`)
  is exercised by at least one scenario (diff/coverage review, not just
  file existence).

**Implementation Note**: After completing this phase and all automated
verification passes, pause here for manual confirmation from the human
that the manual testing was successful.

---

## Testing Strategy

### Unit Tests:

- Write-order: real (unmocked) `run_sync` orchestration against mocked
  I/O boundaries, asserting call order and abort behavior — contract
  layer per test-plan §2.
- No-leakage: real `cli.sync()` → `run_sync()` call chain against mocked
  I/O boundaries that raise sentinel-embedding exceptions, asserting
  absence of the sentinel across all captured output — unit
  (captured-output) layer per test-plan §2.

### Integration Tests:

- None in this phase — real Postgres/Supabase harnesses are Phase 3/4
  per test-plan §3.

### Manual Testing Steps:

1. Swap the write-order blocks in `run_sync`, confirm the new test fails,
   revert.
2. Introduce a `str(exc)` leak in `cli.py`, confirm the new test fails,
   revert.
3. Run `uv run pytest -m "not integration"` and confirm no regressions in
   the rest of the suite after the `test_sync.py` edit.

## Performance Considerations

None — test-only change, no production code path affected.

## Migration Notes

None — no existing data, runtime behavior, or CI configuration changes.

## References

- Test plan: `context/foundation/test-plan.md` §2 (Risk #2, Risk #3),
  §3 Phase 2, §5 Quality Gates
- Write-order orchestration: `dataguard/sync.py:54-66`
- No-leakage boundary: `dataguard/cli.py:45-55`, `dataguard/env_check.py`
- Existing patterns: `tests/test_sync.py:396-511`, `tests/test_cli.py`
- Prior art on write-order correctness: `context/archive/2026-07-24-live-sync-write-cycle/plan.md`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Write-order regression guard

#### Automated

- [x] 1.1 New file passes: `uv run pytest tests/test_write_order_regression.py -v` — 1acbd18
- [x] 1.2 Full suite still passes: `uv run pytest -m "not integration"` — 1acbd18
- [x] 1.3 Type checking passes: `uv run mypy dataguard/` — 1acbd18
- [x] 1.4 Lint and format clean: `uv run ruff format --check . && uv run ruff check .` — 1acbd18

#### Manual

- [x] 1.5 Swapping the write-order call blocks makes the new test fail with a clear message, then revert — 1acbd18
- [x] 1.6 `test_sync.py`'s watermark test no longer references `manager`/`attach_mock` — 1acbd18

### Phase 2: No-leakage regression guard

#### Automated

- [x] 2.1 New file passes: `uv run pytest tests/test_no_leakage_regression.py -v`
- [x] 2.2 Full suite still passes: `uv run pytest -m "not integration"`
- [x] 2.3 Type checking passes: `uv run mypy dataguard/`
- [x] 2.4 Lint and format clean: `uv run ruff format --check . && uv run ruff check .`

#### Manual

- [x] 2.5 Introducing a `str(exc)` leak in `cli.py` makes the new test fail, then revert
- [x] 2.6 Every console.print call site named in test-plan §2 row #3 is exercised by a scenario
