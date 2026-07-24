# Validation Engine (S-02) — Plan Brief

> Full plan: `context/changes/validation-engine/plan.md`
> Research: `context/changes/validation-engine/research.md`

## What & Why

`_classify` in `dataguard/sync.py` is currently a no-op — it always returns `Outcome.VALID` and ignores the rules file entirely, meaning no record is ever actually gated. This plan implements the real field-level validation engine so DataGuard's core promise (validate before write, log every rejection with the exact reason) actually holds for S-02.

## Starting Point

`Outcome`/`RecordResult` are stable and already three-way (VALID/INVALID/ERRORED). `_load_rules` parses JSON with no structural validation. `rules/orders.json` is an empty `{}` placeholder. The CLI's summary table (`cli.py`) already handles all three outcomes correctly and needs no changes. Research (internal + external, via Exa/Context7) already ruled out `jsonschema`/`pydantic`/`cerberus` in favor of a stdlib evaluator.

## Desired End State

`dataguard sync --table orders --rules rules/orders.json` validates every record against a real rules file and classifies it correctly — invalid records carry an exact, human-readable reason; malformed records that crash the evaluator are ERRORED, not silently swallowed; a typo'd rules file fails fast before any DB connection.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Rules JSON syntax | Flat rule-list: `{"rules": [{"field", "check", "value"}]}` | Composes multiple constraints per field naturally, trivial to test per-entry | Plan (research posed the question) |
| Module split | New `dataguard/rules.py`, pure/no I/O | Keeps `sync.py` thin; matches existing `_extract`/`_load_rules` pure-function shape | User directive |
| INVALID vs ERRORED boundary | `required`/`type` failures → INVALID; comparison/regex crashes (wrong Python type) → ERRORED | Keeps ERRORED rare and matches PRD's "malformed/unparseable" framing | Plan |
| Rules-file validation | Structural check (`validate_rules_schema`) at load time, wired into `_load_rules` | Catches a typo'd rules file immediately with a clear message, not a silent misclassification | Plan |
| Type-check strictness | Strict `isinstance`, no coercion (`"42"` fails `type:int`) | Predictable; matches psycopg2's already-typed row values | Plan |
| Multi-violation reporting | First failing rule wins, no aggregation | Matches PRD's singular "the exact rule violation" wording | Plan |
| Reason string format | `"<field>: <constraint description>"` | Matches the example already in research.md's Architecture Insights | Plan |
| Testing | TDD, `tests/test_validation.py` written before implementation each phase | Strict TDD requested; keeps evaluator behavior pinned before wiring | User directive |

## Scope

**In scope:**
- `dataguard/rules.py` — rule evaluator (`evaluate`) + rules-file schema validator (`validate_rules_schema`)
- `_classify` and `_load_rules` in `dataguard/sync.py` rewired to use it
- `rules/orders.json` populated with a realistic example rule set
- `tests/test_validation.py` (new) + `tests/test_sync.py` extensions

**Out of scope:**
- `_connect_target`, `_write_rejections_to_supabase`, `_commit_valid` (S-03)
- `dataguard/cli.py` (summary table already correct)
- Cross-field rules, type coercion, multi-violation aggregation, any new dependency

## Architecture / Approach

`dataguard/rules.py` is a pure module: `evaluate(row, rules) -> (Outcome, reason) | None` and `validate_rules_schema(rules_data) -> list[rules]`, both dependency-free (stdlib `operator`, `re`, `difflib`). `sync.py`'s `_load_rules` calls `validate_rules_schema` after JSON parsing; `_classify` calls `evaluate` and maps its result straight into `RecordResult`. No new dependencies, no change to `Outcome`/`RecordResult`/`cli.py`.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Rule evaluator core | `rules.py`'s `evaluate()`, all 4 check types, TDD via `test_validation.py` | Classification correctness is load-bearing — a bug here silently corrupts Target DB (flagged in roadmap) |
| 2. Rules-file structural validation | `validate_rules_schema()` wired into `_load_rules` | Over-strict validation could reject a technically-workable rules file — mitigated by testing only the 4 documented check types |
| 3. Wire `_classify` + real example rules | Live end-to-end classification, `rules/orders.json` populated | `_load_rules` return type changes (`dict` → `list`) — single call site, but must update in lockstep with `_classify`'s signature |

**Prerequisites:** S-01 (source-db-extraction) — done.
**Estimated effort:** ~1 session across 3 phases; each phase is small and independently testable.

## Open Risks & Assumptions

- `rules/orders.json`'s example fields (`id`, `customer_email`, `amount`, `status`) are illustrative — no real `orders` table schema was confirmed against a live Source DB; adjust field names to match the engineer's actual table before relying on it beyond a smoke test.
- ERRORED is intentionally a narrow, rare path (crash-only) — if real-world data produces far more ERRORED records than expected, that's a signal the rules file is missing `type` rules on the fields that crash, not an evaluator bug.

## Success Criteria (Summary)

- `uv run pytest` and `uv run mypy dataguard/` pass with full coverage of `dataguard/rules.py`.
- A dry-run against real data produces a summary table with correct, non-trivial Passed/Failed/Errored counts — the gate is provably live, not a no-op.
- No record field values ever appear in console output — only field names and constraint descriptions.
