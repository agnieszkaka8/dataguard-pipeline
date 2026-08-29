<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Testing Critical Path Regression Guard

- **Plan**: context/changes/testing-critical-path-regression-guard/plan.md
- **Scope**: Full plan (Phase 1 of 2, Phase 2 of 2)
- **Date**: 2026-08-29
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 3 warnings, 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — No-leakage sweep never checks the exception object itself

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Plan Adherence
- **Location**: tests/test_no_leakage_regression.py:46-49 (`_assert_no_leak`), called with zero `exc_chain` args at every call site
- **Detail**: The plan's Contract for Phase 2 said to "assert the sentinel string is absent from every captured console.print argument and from `str(excinfo.value)` plus `str(excinfo.value.__cause__)` where a cause chain exists." `_assert_no_leak` accepts `*exc_chain` for exactly this, but every one of the 12 tests calls it as `_assert_no_leak(captured)` — the exception object is never passed in. In practice this isn't a silent gap: `cli.sync()`'s `except Exception as exc: ...; raise typer.Exit(code=1)` never uses `raise ... from exc`, so `excinfo.value` is a bare `typer.Exit` with no message and `excinfo.value.__cause__` is always `None` — there was nothing meaningful to check at this boundary. The manual verification step (swapping `type(exc).__name__` for `{type(exc).__name__}: {exc.__cause__}` in `cli.py`) confirmed the console sweep alone catches a real leak. So the implementation is arguably correct; the plan's Contract wording overstated what's checkable at the `typer.Exit` boundary.
- **Fix A ⭐ Recommended**: Drop the unused `*exc_chain` parameter from `_assert_no_leak` and adjust the module docstring to say the sweep covers console output only (the actual leak surface), not exception objects.
  - Strength: Matches what the code actually verifies; removes dead code; grounded in the empirical proof from manual verification.
  - Tradeoff: Narrows the stated guarantee — a future caller of `run_sync()` that bypasses `cli.py`'s try/except entirely wouldn't be covered by this file.
  - Confidence: HIGH — reasoned from `typer.Exit`'s actual structure and confirmed by the swap-and-revert demonstration.
  - Blind spot: Doesn't protect a hypothetical future caller of `run_sync()` that isn't `cli.sync()`.
- **Fix B**: Also call the underlying `sync.py` functions directly (bypassing `cli.sync()`) in each failure scenario and assert `SENTINEL not in str(raised_exception)`, giving real independent coverage of `run_sync`'s own exception messages.
  - Strength: Closes Fix A's blind spot — protects the guarantee even for callers that don't go through `cli.py`.
  - Tradeoff: Roughly doubles this file's size; overlaps with `test_sync.py`'s existing `pytest.raises(RuntimeError, match="...")` assertions on the same messages.
  - Confidence: MEDIUM — real added protection, but `test_sync.py`'s existing `match=` assertions use `re.search` semantics, so an appended leak (e.g. `"Source DB connection failed: <sentinel>"`) would still match today — meaning that backstop isn't as tight as it looks either.
  - Blind spot: Haven't audited whether every existing `match=` assertion in `test_sync.py` is anchored tightly enough to be a real backstop.
- **Decision**: FIXED via Fix A — dropped `*exc_chain` from `_assert_no_leak`, clarified module docstring to state console-only coverage.

### F2 — Rich `Table` renderables aren't swept, contradicting the "any future console.print" claim

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: tests/test_no_leakage_regression.py:38-39 (`captured.extend(str(a) for a in args)`); relevant call site dataguard/cli.py:91 (`console.print(summary)`, a `rich.table.Table`)
- **Detail**: `rich.table.Table` has no custom `__str__`, so `str(summary)` yields a default object repr, not the rendered cell text. A future leak embedded in a `Table`/`Panel`/`Text` renderable would not be caught by this sweep. Currently harmless — the only such call site holds only counts — but the module docstring's claim that any future `console.print`/exception addition is "automatically covered" overstates this.
- **Fix A ⭐ Recommended**: Add a one-line docstring caveat noting the sweep captures only string-representable `console.print` arguments, and that today's one non-string call site (`_print_summary`'s `Table`) is verified by inspection to hold only counts.
  - Strength: Cheap, honest, doesn't touch working test code; matches cost×signal (zero live risk today).
  - Tradeoff: Leaves a real but dormant blind spot for future richer console output (e.g. a table of rejected records).
  - Confidence: HIGH.
  - Blind spot: If DataGuard later adds a Panel/Table with raw record data, this gap goes live and nothing forces a revisit except the docstring note.
- **Fix B**: Rewrite the capture harness to use `Console(record=True)` / `console.export_text()` so every renderable is captured as its actually-rendered text.
  - Strength: Closes the gap completely; matches the file's "generic sweep" design intent.
  - Tradeoff: More invasive — retrofitting `record=True` onto the shared module-level `Console` singletons needs care to avoid state leaking across the 12 test functions; the swap-and-revert proof would need re-running.
  - Confidence: MEDIUM — documented Rich pattern, but not yet verified against this file's specific patch structure.
  - Blind spot: Haven't verified `record=True` on three separately-instantiated `Console` objects composes cleanly with per-test `monkeypatch` teardown.
- **Decision**: FIXED via Fix A — added a docstring caveat noting the sweep only inspects string-representable console.print arguments.

### F3 — Four tests depend on no stray `.watermark` file existing at repo root

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: tests/test_no_leakage_regression.py — `test_no_leak_on_target_connect_failure` (~L92), `test_no_leak_on_supabase_connect_failure` (~L121), `test_no_leak_on_supabase_write_failure` (~L150), `test_no_leak_on_target_write_failure` (~L179)
- **Detail**: These four tests call `cli.sync(..., since=None, ...)` and reach `read_watermark(None)` without monkeypatching `dataguard.watermark._WATERMARK_PATH`. Since `_WATERMARK_PATH = Path(".watermark")` is relative to cwd, they silently depend on no real `.watermark` file existing at the repo root — every other watermark-touching test in this file and the rest of the suite patches `_WATERMARK_PATH` to `tmp_path` specifically to avoid this. Doesn't break today (the leak assertions don't depend on the watermark value), but is a real hermeticity gap versus the rest of the suite's convention.
- **Fix**: Add `monkeypatch.setattr("dataguard.watermark._WATERMARK_PATH", tmp_path / ".watermark")` to the four listed tests, matching the pattern already used elsewhere in this same file.
- **Decision**: FIXED — added the monkeypatch to all 4 tests.
