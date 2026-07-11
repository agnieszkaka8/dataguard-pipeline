<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Source DB Extraction (S-01)

- **Plan**: context/changes/source-db-extraction/plan.md
- **Scope**: All phases (1–3 of 3)
- **Date**: 2026-07-11
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical  4 warnings  5 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | WARNING |
| Pattern Consistency | WARNING |
| Success Criteria | WARNING |

## Findings

### F1 — NO_COLOR not respected in watermark.py

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: dataguard/watermark.py:6
- **Detail**: Console() created without no_color= while sibling modules env_check.py:7 and cli.py:14 pass it explicitly. Hard rule in AGENTS.md: "all rich output must check NO_COLOR".
- **Fix**: Replace Console() with Console(no_color=bool(os.environ.get("NO_COLOR"))), add import os at top.
- **Decision**: FIXED

### F2 — NO_COLOR not respected in sync.py

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: dataguard/sync.py:13
- **Detail**: Same as F1. S-01 added console.print() calls in _connect_source() and run_sync().
- **Fix**: Same fix as F1.
- **Decision**: FIXED

### F3 — Source DB connection never explicitly closed

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: dataguard/sync.py:29
- **Detail**: _connect_source() opens a real psycopg2 connection assigned to _source_conn with no try/finally or close(). Any exception mid-run (currently guaranteed since _classify etc. are stubs) relies on CPython reference counting to close the connection — not reliable across runtimes or complex exception paths.
- **Fix A ⭐ Recommended**: Add try/finally around run_sync body; close _source_conn in finally block.
  - Strength: Explicit and readable; easy to extend for _target_conn in S-03.
  - Tradeoff: Slightly more nesting in run_sync.
  - Confidence: HIGH
  - Blind spot: None significant.
- **Fix B**: Use psycopg2 as context manager (with psycopg2.connect() as conn:)
  - Strength: Idiomatic.
  - Tradeoff: psycopg2 context manager commits/rolls back on exit, not just closes — changes transaction semantics.
  - Confidence: MED
  - Blind spot: S-03 transaction design may conflict.
- **Decision**: FIXED (Fix A)

### F4 — Write-order risk: _connect_target() called before Supabase write

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Architecture
- **Location**: dataguard/sync.py:46 (pre-fix)
- **Detail**: AGENTS.md hard rule requires Supabase rejections before any Target DB commit. The scaffold called _connect_target() before _write_rejections_to_supabase() — if target connection fails, Supabase write is never attempted. Pre-existing scaffold issue that S-01 inherited; S-03 would have triggered the bug when implementing _connect_target().
- **Fix A ⭐ Recommended**: Move _connect_target() call to after the Supabase write.
- **Decision**: FIXED (Fix A)

### F5 — First-run warning not verified in test

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: tests/test_watermark.py (test_read_watermark_missing_file)
- **Detail**: Plan says "Missing file → returns None, first-run message printed." Test only asserted the return value, not the console output.
- **Fix**: Monkeypatch dataguard.watermark.console with a StringIO-backed Console and assert "No .watermark found" appears in output.
- **Decision**: FIXED

### F6 — Deferred import os inside _connect_source()

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: dataguard/sync.py:71 (pre-fix)
- **Detail**: import os appeared inside _connect_source(); import json inside _load_rules(). All other modules use top-level imports.
- **Fix**: Move import os and import json to module-level import block.
- **Decision**: FIXED

### F7 — bare dict in _extract return type instead of dict[str, Any]

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: dataguard/sync.py:88 (pre-fix)
- **Detail**: _extract() annotated list[dict] with # type: ignore[type-arg]. AGENTS.md requires mypy-enforced type annotations on all public functions.
- **Fix**: Change to list[dict[str, Any]], import Any from typing. Same for _classify and _load_rules.
- **Decision**: FIXED

### F8 — Manual verification items 2.3 and 2.4 unchecked

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: context/changes/source-db-extraction/plan.md (Progress §Phase 2)
- **Detail**: Phase 2 has two unchecked manual criteria requiring a live SOURCE_DB: dry-run with valid SOURCE_DB (2.3) and first-run warning visible (2.4). Phase 3 was completed without these being recorded as done.
- **Fix**: Run both checks against a real SOURCE_DB and mark [x] in plan.md.
- **Decision**: PENDING — requires live SOURCE_DB

### F9 — No test for unhandled psycopg2 errors propagating from _extract

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: tests/test_sync.py
- **Detail**: _extract() catches UndefinedColumn but other psycopg2 errors (UndefinedTable, mid-query OperationalError) propagate as raw psycopg2 exceptions. No test verifies this behavior.
- **Decision**: SKIPPED — broad propagation acceptable for now
