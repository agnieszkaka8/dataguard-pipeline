<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Watermark Override Warning (S-04)

- **Plan**: context/changes/watermark-override/plan.md
- **Scope**: All phases (1–2 of 2)
- **Date**: 2026-08-22
- **Verdict**: APPROVED
- **Findings**: 0 critical 1 warning 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — Malformed `--since` crashes with a raw traceback instead of a clean error

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: dataguard/cli.py:39, dataguard/watermark.py:44 (`check_since_override`'s `datetime.fromisoformat(since)`)
- **Detail**: `_guard_since_override(since)` runs at `cli.py:39`, entirely outside the `try/except Exception:` block that wraps `run_sync()` at lines 45-55. It calls `check_since_override(since)`, which does `datetime.fromisoformat(since)` with no error handling. Before this change, `since` was first parsed inside `read_watermark` (called from `run_sync`), which *is* inside that try/except — so a bad `--since` string produced the codebase's standard clean error (`"[red]Sync failed:[/red] ValueError"` + `typer.Exit(1)`). Now the same bad input raises `ValueError` from a call site with no handler at all, propagating as a raw Python traceback — a regression from the established clean-error convention (confirmed directly: `_guard_since_override("not-a-timestamp")` raises `ValueError: Invalid isoformat string: 'not-a-timestamp'` uncaught). Exit code is still non-zero by Python's default, so the AGENTS.md "exit non-zero on every failure" hard rule isn't violated, but the traceback leaks internal detail the rest of this CLI never does. No test covers this path.
- **Fix**: Catch `ValueError` specifically around the `check_since_override` call inside `_guard_since_override`, printing a clean message and exiting — leave `typer.Abort` unhandled so a declined confirmation still propagates to Click's own abort handling (don't widen this to `except Exception`, which would swallow `typer.Abort` too, since `typer.Abort` is itself an `Exception`/`RuntimeError` subclass):
  ```python
  def _guard_since_override(since: Optional[str]) -> None:
      if since is None:
          return
      try:
          risky = check_since_override(since)
      except ValueError:
          console.print(f"[red]Invalid --since timestamp:[/red] {since}")
          raise typer.Exit(code=1)
      if risky:
          console.print(
              f"[bold red]Warning:[/bold red] --since {since} predates the current "
              "watermark. This will re-process already-synced records and may "
              "create duplicates in Target DB."
          )
          typer.confirm("Continue anyway?", abort=True)
  ```
  Add a regression test asserting a malformed `--since` produces `typer.Exit(code=1)` with the clean message, not an uncaught `ValueError`.
- **Decision**: FIXED — pending commit. `_guard_since_override` now catches `ValueError` around `check_since_override`, prints `"Invalid --since timestamp: <value>"`, and raises `typer.Exit(code=1)`; `typer.Abort` remains unhandled. Regression test `test_guard_since_override_invalid_since_exits_cleanly` added (exercises the real `check_since_override`, not mocked). Verified directly: `_guard_since_override("not-a-timestamp")` now exits cleanly with code 1 instead of raising uncaught.

## Compliant / no issue found

- Call order: `_guard_since_override` runs before `rules.exists()` and before `run_sync` (which owns `_connect_source`) — no DB connection is attempted before the guard, correct per FR-008 intent.
- Exit code on decline: `typer.confirm(..., abort=True)` raises `typer.Abort` from outside the `try/except Exception` block, so it reaches Click's own `Abort` handling and exits non-zero as intended; `test_guard_since_override_propagates_abort_on_decline` asserts this correctly.
- No leakage: the warning only echoes the user-supplied `--since` string, never connection strings, credentials, or raw record field values.
- Timezone correctness: `check_since_override` normalizes via `.astimezone(timezone.utc)`, and `_read_stored_watermark` always returns tz-aware UTC or `None` — the comparison is always aware-vs-aware, no `TypeError` risk.
- Pattern compliance: `_read_stored_watermark`/`check_since_override` match `read_watermark`/`write_watermark`'s docstring style, naming, and type annotations; `_guard_since_override` matches `_print_summary`'s placement and use of the shared module-level `console`. No new `Console()` instantiation added anywhere (per [[always-pass-no-color-to-rich-console]] lesson).
- `tests/test_cli.py` (new file) follows the same `patch("dataguard.cli.<name>", ...)` / `assert_called_once_with` conventions already used in `tests/test_sync.py`.
- Plan adherence: both phases' contracts verified byte-for-byte MATCH against the plan; no scope creep against the "What We're NOT Doing" list; diff footprint exactly `dataguard/watermark.py`, `dataguard/cli.py`, `tests/test_watermark.py`, `tests/test_cli.py` as planned.
- All automated success criteria re-verified passing at review time: 83 tests, mypy clean, ruff clean.
- 4 manual verification items (interactive TTY confirm/decline flow) remain unchecked — documented as deferred pending manual testing outside this autonomous run, consistent with prior slices' precedent.
