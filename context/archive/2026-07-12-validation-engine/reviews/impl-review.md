<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Validation Engine (S-02) Implementation Plan

- **Plan**: context/changes/validation-engine/plan.md
- **Scope**: Phase 1-3 of 3 (full plan)
- **Date**: 2026-07-24
- **Verdict**: REJECTED
- **Findings**: 1 critical, 1 warning, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | FAIL |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — Malformed regex pattern in rules file crashes mid-run instead of failing fast

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: dataguard/rules.py:63-74 (`_check_regex`), dataguard/rules.py:109-152 (`validate_rules_schema`)
- **Detail**: `validate_rules_schema` only checks that a `regex` rule has a `"value"` key present — it never verifies the pattern actually compiles. `_check_regex` only catches `TypeError`, but an invalid pattern (e.g. `"(unclosed"`) raises `re.error`, which is a plain `Exception` subclass, not `TypeError`. Confirmed live: `validate_rules_schema({"rules": [{"field": "email", "check": "regex", "value": "(unclosed"}]})` passes cleanly, then `evaluate(...)` on that same ruleset raises `re.error: missing ), unterminated subpattern` uncaught.

  This directly undermines the exact design goal Phase 2 established: since Phase 2's reordering fix, `_load_rules` (which calls `validate_rules_schema`) now runs *before* `_connect_source()`/`_extract()` specifically so a malformed rules file fails fast before any DB work happens. A bad regex slips past that gate, so the crash instead surfaces mid-batch inside the `_classify` list comprehension in `run_sync` (dataguard/sync.py:46) — *after* Source DB connection and extraction have already run. The CLI still exits non-zero (caught by cli.py's try/except), so nothing is silently swallowed, but the fail-fast guarantee Phase 2 was built to deliver is broken for this one rule type.
- **Fix**: In `validate_rules_schema` (dataguard/rules.py, inside the per-rule loop), when `check == "regex"`, wrap `re.compile(rule["value"])` in a try/except and raise `RuntimeError` with the rule index on `re.error` — mirrors the existing unknown-check/unknown-type validation pattern already in the same function.
- **Decision**: FIXED — added `re.compile()` validation catching `(re.error, TypeError)` in `validate_rules_schema` (dataguard/rules.py); 3 new tests added to tests/test_validation.py; full suite (60/60), mypy, and ruff all verified green.

### F2 — `evaluate()` has no defensive guard against unvalidated rules

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: dataguard/rules.py:90-99 (`evaluate`)
- **Detail**: `evaluate()` indexes `rule["field"]`, `_CHECKS[rule["check"]]`, `rule["value"]`, and `_TYPE_NAMES[type_name]` with no guards. This is safe today only because the sole production caller (`_load_rules` in dataguard/sync.py) always runs `validate_rules_schema` first. The function is documented as a "pure rule evaluator" but its docstring doesn't state this schema-validated precondition, so a future caller passing raw/unvalidated rules would get an uncaught `KeyError` instead of a clean `RuntimeError`.
- **Fix**: Add one line to `evaluate()`'s docstring noting it expects schema-validated rules (as produced by `validate_rules_schema`) — no defensive runtime guard needed, since adding one would duplicate validation logic against this project's "no premature abstraction" convention and the only production caller already validates first.
- **Decision**: FIXED — docstring precondition note added to `evaluate()` (dataguard/rules.py).

### F3 — Regex ERRORED message misattributes non-string pattern failures to the row value

- **Severity**: 👁️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: dataguard/rules.py:63-74 (`_check_regex`)
- **Detail**: If `rule["value"]` (the pattern) is non-string rather than the row's value, `re.search` still raises `TypeError`, and the resulting message (`f"{field}: cannot apply regex to {type(value).__name__}"`) blames the row value's type rather than the malformed pattern. Low-impact edge case; F1's fix (validating the pattern compiles at schema-load time) would catch most instances of this before it ever reaches `_check_regex`.
- **Fix**: No action needed beyond F1 — noting for awareness only.
- **Decision**: SKIPPED — F1's fix already closes this gap (validate_rules_schema now catches non-string regex patterns via re.compile's TypeError too, before this code path is reachable).

## Post-Triage Status

All findings resolved as of 2026-07-24: F1 fixed (regex-compile validation added, closing the fail-fast gap), F2 fixed (docstring precondition documented), F3 skipped as subsumed by F1's fix. Post-fix verification: 60/60 tests pass, mypy clean, ruff clean. Safety & Quality dimension is now effectively PASS; original REJECTED verdict reflects pre-triage state and is retained here as the historical record of what the review found.

## Notes

- Plan Drift Detection sub-agent found all 3 phases MATCH the plan, including all "Critical Implementation Details" (rule schema, INVALID/ERRORED boundary, reason string format) and all "What We're NOT Doing" boundaries respected (cli.py untouched, no new dependency, stub functions still raise NotImplementedError). One cosmetic note: the `type` check's bool exclusion uses `type(value) is bool` rather than the plan contract's literal `isinstance(value, bool)` wording — behaviorally identical since `bool` cannot be subclassed in CPython, not counted as drift.
- Safety/Pattern sub-agent confirmed no field-value leakage anywhere in rules.py/sync.py (AGENTS.md hard rule), correct write-order preservation, and full pattern consistency with existing modules (typed functions, RuntimeError convention, test style, correct absence of Console(no_color=...) in rules.py since it does no I/O).
- All automated success criteria verified directly: `uv run pytest -m "not integration"` → 57 passed; `uv run mypy dataguard/` → clean; `uv run ruff format --check .` and `uv run ruff check .` → clean. The one failing test in the unfiltered `uv run pytest` run (`test_extract_real_db`) is a pre-existing `@pytest.mark.integration` test requiring a live `SOURCE_DB`, unrelated to this change (confirmed via `git stash` during Phase 1).
