# Validation Engine (S-02) Implementation Plan

## Overview

`_classify` in `dataguard/sync.py` is a no-op stub that always returns `Outcome.VALID` and ignores the `rules` argument entirely. This plan implements the real field-level validation engine: a new `dataguard/rules.py` module that evaluates a rules JSON file's four MVP rule types (required, type, comparison, regex) against each extracted record, wires that evaluator into `_classify` and `_load_rules`, and populates `rules/orders.json` with a realistic example. Built test-first, with `tests/test_validation.py` as the primary suite.

## Current State Analysis

- `Outcome` (VALID/INVALID/ERRORED) and `RecordResult` (`row_id`, `outcome`, `reason: str | None`) are already defined and stable ([dataguard/models.py](../../../dataguard/models.py)) — this plan does not change them.
- `_classify(row, rules) -> RecordResult` is called once per row in `run_sync` ([dataguard/sync.py:45](../../../dataguard/sync.py#L45)) and is currently `return RecordResult(row_id=row.get("id"), outcome=Outcome.VALID)` — a true no-op.
- `_load_rules(path)` ([dataguard/sync.py:116-120](../../../dataguard/sync.py#L116-L120)) parses JSON and raises `RuntimeError("Rules file is not valid JSON")` on syntax errors only — it performs no structural validation of the rules content.
- `rules/orders.json` is an empty placeholder (`{}`).
- The CLI's summary table (`_print_summary` in [dataguard/cli.py:58-75](../../../dataguard/cli.py#L58-L75)) already has dedicated Total/Passed/Failed/Errored columns and already exits non-zero when `failed > 0 or errored > 0` — **no changes needed there**. This plan's scope ends at `_classify` producing correct `RecordResult`s.
- No existing tests exercise `_classify` — this is genuinely greenfield (confirmed in `research.md`).
- Conventions to follow: `Console(no_color=bool(os.environ.get("NO_COLOR")))` for any new Rich output ([lessons.md](../../../context/foundation/lessons.md)); typed public functions (mypy-enforced); no premature abstraction (AGENTS.md/CLAUDE.md).

## Desired End State

Running `dataguard sync --table orders --rules rules/orders.json` validates every extracted record against real rules and classifies it as `VALID`, `INVALID` (with an exact rule-violation reason), or `ERRORED` (with a crash reason) — no record is silently passed through. `dataguard/rules.py` owns rule evaluation and rules-file schema validation as pure, testable functions; `sync.py`'s `_classify` and `_load_rules` become thin callers into it.

**Verification**: `uv run pytest` passes with full coverage of `dataguard/rules.py` in `tests/test_validation.py`; `uv run mypy dataguard/` passes; running `dataguard sync --table orders --rules rules/orders.json --dry-run` against a live Source DB produces a summary table with non-trivial Passed/Failed counts (not 100% passed, proving the gate is live).

### Key Discoveries:

- `_extract` returns `list[dict[str, Any]]` with native Python-typed values (psycopg2 already converts DB types) — no JSON string parsing happens at the row level, only at the rules-file level. This is why "type" checks must be strict `isinstance` against Python types, not string coercion.
- `RecordResult.reason` is a single `str | None` — confirms the first-violation-wins design: one rule, one reason, no aggregation format needed.
- Nothing downstream of `_classify` (future `_write_rejections_to_supabase`) expects more than `RecordResult` — this plan does not touch write-order logic.

## What We're NOT Doing

- Not implementing `_connect_target`, `_write_rejections_to_supabase`, or `_commit_valid` (S-03 scope).
- Not touching `dataguard/cli.py` — the summary table already handles all three outcomes correctly.
- Not supporting cross-field rules (explicitly deferred to v2 per PRD Non-Goals).
- Not adding a validation library dependency (`jsonschema`/`pydantic`/`cerberus`) — research concluded a stdlib evaluator is sufficient; `pyproject.toml` dependencies are unchanged.
- Not adding type coercion (e.g. numeric-string-to-int) — type checks are strict `isinstance`.
- Not aggregating multiple rule violations per record into one reason string — first failing rule wins.

## Implementation Approach

Build `dataguard/rules.py` bottom-up and test-first: pure per-check evaluation functions first (Phase 1), then a rules-file structural validator that reuses the same check-name registry (Phase 2), then wire both into `sync.py` and produce a real example rules file (Phase 3). Each phase's automated verification gate (tests + mypy) must pass before the next phase starts, per the TDD approach requested.

## Critical Implementation Details

### Rule schema (locked)

```json
{
  "rules": [
    { "field": "age", "check": "required" },
    { "field": "age", "check": "type", "value": "int" },
    { "field": "age", "check": "gte", "value": 0 },
    { "field": "email", "check": "regex", "value": "^[^@]+@[^@]+\\.[^@]+$" }
  ]
}
```

- `check` is one of: `required`, `type`, `gte`, `lte`, `gt`, `lt`, `eq`, `ne`, `regex`.
- `type`'s `value` is one of the strings: `"str"`, `"int"`, `"float"`, `"bool"`, `"number"` (accepts `int` or `float`, excluding `bool`).
- Comparison checks (`gte`/`lte`/`gt`/`lt`/`eq`/`ne`) map to `operator.ge`/`le`/`gt`/`lt`/`eq`/`ne`.
- `required` fails when the field is absent from the row dict OR its value is `None`. It does not fail on empty string — that's a separate concern not in MVP scope.
- Rules are evaluated **in file order per record**; evaluation stops at the first failing rule (`required` fails first if both `required` and `type` target the same field and the field is missing — don't evaluate `type` against a missing field).

### VALID / INVALID / ERRORED boundary (locked)

- `required` and `type` failures (field missing/None, or wrong Python type) → **INVALID**. These are expected, anticipated validation failures — never crash the evaluator.
- Comparison or regex checks applied to a value where the operation itself raises (e.g. `gte` between `str` and `int`, `regex` against a non-`str`/non-`bytes` value) → the evaluator catches the exception and returns **ERRORED**, not INVALID. This is the only path to `Outcome.ERRORED` at the classify layer.
- Because `type` is checked as its own rule (typically before a `gte`/`regex` rule on the same field in a well-formed rules file), a record with a `type`-mismatched field only reaches ERRORED if the rules file omits the corresponding `type` rule — a rules-authoring gap, not a bug. Document this in the rules-file guidance (a comment convention in `rules/orders.json` or a note in the module docstring), not enforced in code.

### Reason string format (locked)

`"<field>: <constraint description>"`, e.g.:
- `"age: is required"`
- `"age: expected int, got str"`
- `"age: must be >= 0"`
- `"email: does not match pattern"`
- ERRORED reasons follow the same shape: `"age: cannot apply regex to list"`

## Phase 1: Rule evaluator core (`dataguard/rules.py`)

### Overview

A pure, dependency-free rule evaluator: given one row (`dict[str, Any]`) and a parsed rules list, return the first violation as an `Outcome` + reason, or `None` if the row passes every rule. TDD: write `tests/test_validation.py` first, covering every check type and the ERRORED boundary, then implement to make tests pass.

### Changes Required:

#### 1. Test suite (written first)

**File**: `tests/test_validation.py`

**Intent**: Exercise every check type (`required`, `type`, all six comparison operators, `regex`) in both passing and failing form, the first-violation-wins ordering, strict (non-coercive) type checking, and the ERRORED boundary (comparison/regex against an incompatible Python type).

**Contract**: Tests call the Phase 1 public function directly (see Contract below) — no mocking needed since the evaluator is pure. Follow the existing table-style test structure from `tests/test_sync.py` (plain `def test_...` functions, grouped with `# ---` comment banners per check type).

#### 2. Rule evaluator implementation

**File**: `dataguard/rules.py` (new)

**Intent**: Implement the four MVP rule types as a small, pure evaluator with no I/O and no hidden state — matching the existing `_extract`/`_load_rules` shape noted in research.md's Architecture Insights.

**Contract**:
```python
def evaluate(row: dict[str, Any], rules: list[dict[str, Any]]) -> tuple[Outcome, str] | None:
    """Return (Outcome.INVALID | Outcome.ERRORED, reason) for the first failing rule
    that applies to this row's fields, or None if every rule passes."""
```
- Internally dispatch on `rule["check"]` via a `dict[str, Callable]` registry mapping check name → handler, avoiding an if/elif chain (matches "no premature abstraction" while keeping dispatch flat and testable per-entry).
- Comparison handlers wrap `operator.ge/le/gt/lt/eq/ne` and catch `TypeError` to signal ERRORED.
- The `regex` handler catches `TypeError` (non-`str` input to `re.search`) to signal ERRORED; uses `re.search`, not `re.match`, so patterns aren't implicitly anchored at the start.
- The `type` handler's `bool` exclusion for `"number"` requires an explicit `isinstance(value, bool)` check before the `isinstance(value, (int, float))` check, since `bool` is an `int` subclass in Python.

### Success Criteria:

#### Automated Verification:

- [ ] All new tests pass: `uv run pytest tests/test_validation.py -v`
- [ ] Type checking passes: `uv run mypy dataguard/rules.py`
- [ ] Linting passes: `uv run ruff check dataguard/rules.py tests/test_validation.py`
- [ ] Full existing suite still passes (no regressions): `uv run pytest`

#### Manual Verification:

- [ ] Spot-check a handful of `evaluate()` calls in a `python -c` / REPL session against hand-built rows and rules to confirm reason strings read naturally.

---

## Phase 2: Rules-file structural validation

### Overview

Add a schema-shape check for the rules file content itself — reusing Phase 1's check-name registry — so a malformed rules file (unknown `check`, missing `field`/`value` keys) fails fast with a clear error before any DB connection is attempted, rather than silently misclassifying every row or crashing mid-run.

### Changes Required:

#### 1. Tests (written first)

**File**: `tests/test_validation.py`

**Intent**: Cover a valid rules file (passes), an unknown `check` name, a rule missing `field`, a `comparison`/`type`/`regex` rule missing `value`, and a non-list `rules` key — each producing a `RuntimeError` with a message naming the offending rule (by index) and the problem.

**Contract**: Extend the same file from Phase 1 with a new `# --- validate_rules_schema ---` section.

#### 2. Schema validator

**File**: `dataguard/rules.py`

**Intent**: A `validate_rules_schema` function that walks the parsed rules list and raises `RuntimeError` with a specific, actionable message on the first structural problem found — mirroring `_load_rules`'s existing `RuntimeError` convention so `sync.py`'s error handling doesn't need a new exception type.

**Contract**:
```python
def validate_rules_schema(rules_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate structure and return the rules list, or raise RuntimeError."""
```
- Checks: `rules_data` has a `"rules"` key whose value is a `list`; every entry is a `dict` with `field: str` and `check` in the known registry; `type`/comparison/`regex` checks additionally require a `value` key (`required` does not).
- Error message format: `f"Rules file is invalid — rule #{index} {problem}"` (matches the format already confirmed in the design question, e.g. `"rule #3 has unknown check 'regexp' (did you mean 'regex'?)"` — implement a simple closest-match suggestion via `difflib.get_close_matches` against the known check names when the check name is unknown, falling back to no suggestion if none is close).

#### 3. Wire into `_load_rules`

**File**: `dataguard/sync.py`

**Intent**: `_load_rules` calls `validate_rules_schema` after JSON parsing succeeds, so both syntax and structural errors are caught before extraction begins.

**Contract**: `_load_rules` return type changes from `dict[str, Any]` to `list[dict[str, Any]]` (the validated rules list, not the wrapping `{"rules": [...]}` dict) — update its one call site in `run_sync` (`rules = _load_rules(rules_path)`, passed straight through to `_classify`) and its type hint accordingly.

### Success Criteria:

#### Automated Verification:

- [ ] All new tests pass: `uv run pytest tests/test_validation.py -v`
- [ ] `_load_rules` tests pass (add coverage in `tests/test_sync.py` for the malformed-rules-file path): `uv run pytest tests/test_sync.py -v`
- [ ] Type checking passes: `uv run mypy dataguard/`
- [ ] Full suite passes: `uv run pytest`

#### Manual Verification:

- [ ] Run `dataguard sync --table orders --rules <a hand-crafted malformed rules file>` and confirm the CLI exits non-zero with a clear, specific error message (not a stack trace) before any "Connecting to Source DB" message prints.

---

## Phase 3: Wire `_classify` and ship a real example rules file

### Overview

Replace the `_classify` no-op with a real call into `rules.evaluate`, and replace the empty `rules/orders.json` placeholder with a realistic rule set an engineer could actually use, proving the end-to-end path with real data shapes.

### Changes Required:

#### 1. Tests (written first)

**File**: `tests/test_sync.py`

**Intent**: Add `_classify` integration tests — a row that passes all rules → `Outcome.VALID` with `reason=None`; a row that fails a `required` rule → `Outcome.INVALID` with the expected reason; a row that triggers the ERRORED path (e.g. `regex` against a list-typed field) → `Outcome.ERRORED`. Also verify `row_id` is still taken from `row.get("id")` regardless of outcome.

**Contract**: New `# --- _classify ---` section in `tests/test_sync.py`, following the existing plain-function test style (no new fixtures needed — `_classify` takes plain dicts).

#### 2. `_classify` implementation

**File**: `dataguard/sync.py`

**Intent**: `_classify` calls `rules.evaluate(row, rules)`; if it returns `None`, the record is `Outcome.VALID`; otherwise the returned `(Outcome, reason)` tuple populates `RecordResult` directly.

**Contract**: `_classify(row: dict[str, Any], rules: list[dict[str, Any]]) -> RecordResult` — signature's `rules` parameter type changes from `dict[str, Any]` to `list[dict[str, Any]]` to match Phase 2's `_load_rules` return type.

#### 3. Example rules file

**File**: `rules/orders.json`

**Intent**: Replace the empty `{}` placeholder with a realistic rule set for an `orders` table, exercising all four check types so it doubles as living documentation of the schema.

**Contract**: Rules covering plausible order fields — `id` (required, type int), `customer_email` (required, regex), `amount` (required, type number, gte 0), `status` (required, type str) — using the exact JSON shape locked in Critical Implementation Details.

### Success Criteria:

#### Automated Verification:

- [ ] All `_classify` tests pass: `uv run pytest tests/test_sync.py -v`
- [ ] Full suite passes: `uv run pytest`
- [ ] Type checking passes: `uv run mypy dataguard/`
- [ ] Linting passes: `uv run ruff format --check . && uv run ruff check .`
- [ ] `rules/orders.json` parses and passes `validate_rules_schema` (covered by a dedicated test loading the real file, not just fixtures)

#### Manual Verification:

- [ ] Run `dataguard sync --table orders --rules rules/orders.json --dry-run` against a real or test Source DB with a mix of valid and invalid rows; confirm the summary table shows non-zero Passed and Failed/Errored counts as appropriate, and confirm no raw field values leak into console output (only field names and constraint descriptions appear in any printed reason).

---

## Testing Strategy

### Unit Tests:

- Every check type (`required`, `type` incl. `bool`/`number` edge cases, all 6 comparison operators, `regex`) in both passing and failing form — `tests/test_validation.py`.
- `validate_rules_schema` against valid and each class of malformed rules file — `tests/test_validation.py`.
- `_classify` integration across VALID/INVALID/ERRORED — `tests/test_sync.py`.

### Integration Tests:

- Existing `@pytest.mark.integration` pattern from `test_sync.py` is not extended in this plan (no new DB-touching code) — Phase 3's manual verification covers the real end-to-end path instead.

### Manual Testing Steps:

1. Run `dataguard sync --table orders --rules rules/orders.json --dry-run` against a Source DB seeded with a mix of clean and broken rows; confirm the summary table splits them correctly.
2. Hand-edit `rules/orders.json` to introduce a typo'd `check` name; confirm the CLI fails fast with the suggestion message before connecting to any DB.
3. Confirm no record field values (only field names and rule descriptions) ever appear in console output, per the AGENTS.md "no field-value leakage" hard rule.

## Performance Considerations

None beyond what's already covered by the PRD's throughput NFR (10,000 records / 60s) — `evaluate()` is O(rules × 1) per record with early exit on first failure, negligible compared to DB I/O.

## Migration Notes

Not applicable — `rules/orders.json` was an unused empty placeholder; no existing rules file content to migrate.

## References

- Research: `context/changes/validation-engine/research.md`
- `dataguard/models.py:6-16` — `Outcome`, `RecordResult`
- `dataguard/sync.py:45,116-124` — `_classify` call site, `_load_rules`, `_classify` stub
- `dataguard/cli.py:58-75` — existing summary table (unchanged by this plan)
- `context/foundation/prd.md` § Business Logic, FR-002 — three-outcome contract, rule scope
- `context/foundation/lessons.md` — Rich `Console(no_color=...)` convention

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Rule evaluator core (`dataguard/rules.py`)

#### Automated

- [x] 1.1 All new tests pass: `uv run pytest tests/test_validation.py -v`
- [x] 1.2 Type checking passes: `uv run mypy dataguard/rules.py`
- [x] 1.3 Linting passes: `uv run ruff check dataguard/rules.py tests/test_validation.py`
- [x] 1.4 Full existing suite still passes: `uv run pytest`

#### Manual

- [x] 1.5 Spot-check `evaluate()` calls in a REPL against hand-built rows and rules

### Phase 2: Rules-file structural validation

#### Automated

- [ ] 2.1 All new tests pass: `uv run pytest tests/test_validation.py -v`
- [ ] 2.2 `_load_rules` malformed-file tests pass: `uv run pytest tests/test_sync.py -v`
- [ ] 2.3 Type checking passes: `uv run mypy dataguard/`
- [ ] 2.4 Full suite passes: `uv run pytest`

#### Manual

- [ ] 2.5 Malformed rules file fails fast with a clear error before any DB connection

### Phase 3: Wire `_classify` and ship a real example rules file

#### Automated

- [ ] 3.1 All `_classify` tests pass: `uv run pytest tests/test_sync.py -v`
- [ ] 3.2 Full suite passes: `uv run pytest`
- [ ] 3.3 Type checking passes: `uv run mypy dataguard/`
- [ ] 3.4 Linting passes: `uv run ruff format --check . && uv run ruff check .`
- [ ] 3.5 `rules/orders.json` parses and passes `validate_rules_schema`

#### Manual

- [ ] 3.6 Real/test Source DB dry-run shows correct Passed/Failed/Errored split, no field-value leakage in console output
