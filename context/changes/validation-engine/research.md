---
date: 2026-07-12T00:00:00Z
researcher: Claude (10x-research)
git_commit: a3754ceb42f3964fb05f25a7beb31d744d550829
branch: main
repository: dataguard-piepline
topic: "S-02 validation engine — rules format and validation approach (stdlib vs external library)"
tags: [research, codebase, validation-engine, sync, models, rules]
status: complete
last_updated: 2026-07-12
last_updated_by: Claude (10x-research)
---

# Research: S-02 validation engine — rules format and validation approach

**Date**: 2026-07-12T00:00:00Z
**Researcher**: Claude (10x-research)
**Git Commit**: a3754ceb42f3964fb05f25a7beb31d744d550829
**Branch**: main
**Repository**: dataguard-piepline

## Research Question

Analyze the current codebase (`Outcome`/`RecordResult` in `dataguard/models.py`, the `_classify` stub in `dataguard/sync.py`, and the `rules/` sample files) to understand the expected validation rules format, then evaluate the best architectural approach for S-02: a lightweight standard-library rule evaluator, or an external library (`jsonschema`, `pydantic`, `cerberus`) — backed by current external best-practice and API research where an external library is considered.

## Summary

`_classify` is currently a no-op stub that always returns `Outcome.VALID` and ignores the rules argument entirely ([dataguard/sync.py:123-124](../../../dataguard/sync.py#L123-L124)). There is no existing validation dependency in `pyproject.toml`, and the rules file format is genuinely undecided — `rules/orders.json` is just `{}` and `rules/.gitkeep` only points back to the PRD ([rules/.gitkeep](../../../rules/.gitkeep)). The roadmap explicitly flags this as Open Question #2, blocking S-02.

**Recommendation: a small standard-library rule evaluator, not an external dependency.**

The PRD (FR-002, Business Logic) scopes rules to exactly four field-level checks — null/required, type, comparison operators, regex — with cross-field rules explicitly out of scope for MVP. That's a narrow, closed rule set that `re`, `operator`, and `isinstance` cover completely in well under 100 lines, with full control over the "exact rule violation reason" the PRD's Business Logic section requires per record. Evaluated externally:

- **Cerberus** has an unresolved, multi-year maintainer-acknowledged maintenance gap (confirmed via web research below) — a real project (`molecule`) publicly dropped it in favor of `jsonschema` for this exact reason. Not a safe new dependency to introduce.
- **jsonschema** is well-maintained and its Draft 2020-12 vocabulary (`type`, `required`, `pattern`, `minimum`/`maximum`) maps cleanly onto the four PRD rule types — a credible second choice if the team later wants a standardized, portable rules format. But it adds a dependency and JSON Schema vocabulary/error-message translation overhead for a rule set this narrow, and generic messages like `"'foo' is not of type 'integer'"` need extra mapping work to become the exact, custom reason strings the Business Logic section calls for.
- **pydantic** is the wrong shape of tool here: rules arrive as data at runtime from a user-supplied JSON file, not as a fixed Python type definition. Using it would mean dynamically building models with `create_model` plus custom validators per rule type — more machinery than the four-rule-type MVP scope justifies, and heavier than the "typed CLI, no premature abstraction" convention this codebase follows.

This conclusion also aligns with the project's own conventions: `AGENTS.md` and `CLAUDE.md` both favor minimal, typed, dependency-light code over premature abstraction, and no validation library is listed among the existing dependencies in `pyproject.toml`.

## Detailed Findings

### `Outcome` and `RecordResult` (dataguard/models.py)

- `Outcome` is a `str` `Enum` with exactly three members: `VALID`, `INVALID`, `ERRORED` ([dataguard/models.py:6-9](../../../dataguard/models.py#L6-L9)). This mirrors the PRD's three-outcome contract exactly (§ Business Logic).
- `RecordResult` is a plain `@dataclass` with `row_id: Any`, `outcome: Outcome`, and `reason: str | None = None` ([dataguard/models.py:12-16](../../../dataguard/models.py#L12-L16)). The `reason` field is optional at the type level — only `VALID` results should leave it `None`; `INVALID` and `ERRORED` results must populate it with the "exact rule violation" (PRD § Business Logic) or the parse/type error, respectively. Whatever validation approach S-02 picks must produce a single human/machine-readable string here, not a structured error object — nothing downstream (the future Supabase writer, `_write_rejections_to_supabase`) expects more than a string.
- No other module currently constructs `RecordResult` with a non-`None` reason, so there's no existing convention to match beyond the dataclass shape itself.

### `_classify` stub (dataguard/sync.py)

- Current implementation: `return RecordResult(row_id=row.get("id"), outcome=Outcome.VALID)` ([dataguard/sync.py:123-124](../../../dataguard/sync.py#L123-L124)) — always valid, rules argument (`rules: dict[str, Any]`) is unused. This is a genuine no-op stub, not a partial implementation to preserve.
- `_classify` is called once per row in a list comprehension in `run_sync`: `results = [_classify(row, rules) for row in records]` ([dataguard/sync.py:45](../../../dataguard/sync.py#L45)). `rules` comes from `_load_rules(rules_path)`, which just does `json.loads(path.read_text())` and raises `RuntimeError("Rules file is not valid JSON")` on parse failure ([dataguard/sync.py:116-120](../../../dataguard/sync.py#L116-L120)) — so `_classify` receives an arbitrary parsed JSON `dict`, not a validated or typed structure. Whatever rules format S-02 settles on, `_load_rules` performs no schema validation of the rules file itself today; that would need to be layered in (or accepted as a risk) alongside `_classify`.
- `results` then feeds directly into the `invalid`/`valid` split (`sync.py:47-48`) and downstream write-order logic (log rejections to Supabase, then commit valid records) — `_classify`'s output contract (`Outcome` + `reason`) is the sole interface the rest of the pipeline depends on. No cross-field context (e.g., other rows, prior results) is passed in — `_classify` is a pure per-row, per-rules function, consistent with the PRD's field-level-only scope.
- No existing tests exercise `_classify` — `tests/test_sync.py` only covers `_connect_source` and `_extract` ([tests/test_sync.py](../../../tests/test_sync.py)). This is genuinely greenfield; there's no existing behavior to avoid breaking.

### Rules file format (rules/)

- `rules/orders.json` is an empty placeholder object: `{}` ([rules/orders.json](../../../rules/orders.json)).
- `rules/.gitkeep` only says: "Validation rule files go here — e.g. rules/orders.json. See context/foundation/prd.md § Business Logic for the rules schema." ([rules/.gitkeep](../../../rules/.gitkeep)) — but the PRD's Business Logic section does not define a concrete JSON shape; it only describes the *inputs/outputs* conceptually. The actual schema is not written down anywhere yet.
- The only concrete constraint on the rules format comes from **FR-002** in the PRD: rules are "simple field-level validations for MVP (null checks, type checks, comparison operators, regex)"; cross-field rules are explicitly deferred to v2 ([context/foundation/prd.md:64-65](../../../context/foundation/prd.md#L64-L65)).
- **Roadmap confirms this is an open, blocking decision**: "Rules JSON schema — formal definition of the field-level rules format (null check, type check, comparison operators, regex). `rules/orders.json` is an empty placeholder. Owner: developer. Block: S-02 implementation decision." ([context/foundation/roadmap.md:126](../../../context/foundation/roadmap.md#L126)). This research is the direct input to resolving that blocker.

### Dependency baseline (pyproject.toml)

- Current dependencies: `psycopg2-binary`, `python-dotenv`, `rich`, `supabase`, `typer` ([pyproject.toml](../../../pyproject.toml)). Dev group: `mypy`, `pytest`, `ruff`, `types-psycopg2`. **No validation library (`jsonschema`, `pydantic`, `cerberus`, or otherwise) is present.** Adopting one is a net-new dependency decision, not a version bump.

## External Research

### Cerberus — maintenance status (via Exa)

A GitHub issue on `pyeve/cerberus` (#577, "Regarding the maintenance of the Cerberus package") shows the maintainer confirming in 2022 that no bug fix had landed since March of the prior year, that they hadn't allocated time for further development, and that they were "open to recruiting maintainer/s." A commenter reports having dropped Cerberus from the `molecule` project specifically "due to lack of maintenance," migrating to `jsonschema` instead (PR title referenced: "Migrate cerberus to jsonschema"). There is no evidence of the situation having resolved since. **Conclusion: Cerberus carries real maintenance risk and should not be adopted as a new dependency for this project.**

### jsonschema vs. Cerberus schema shape (via Exa)

Cerberus's schema format (`type`, `required`, `regex`, `minlength`/`maxlength`, `minimum`/`maximum`, per-field `v.errors` reporting) maps almost one-to-one onto the PRD's four rule types, which is why it looks attractive on paper — but the maintenance finding above rules it out regardless of fit. `jsonschema`'s Draft 2020-12 vocabulary (`type`, `required`, `pattern`, `minimum`/`maximum`) covers the same four rule types and remains actively maintained, making it the credible external fallback if a standardized rules format is wanted later.

### jsonschema — current API contract (via Context7, `/python-jsonschema/jsonschema`, v4.25.1)

Confirmed non-deprecated, current usage pattern for per-field error extraction:

```python
from jsonschema import Draft202012Validator

v = Draft202012Validator(schema)
errors = sorted(v.iter_errors(instance), key=lambda e: e.path)
for error in errors:
    print(list(error.path), error.message)
```

- `Draft202012Validator(schema).iter_errors(instance)` is the current idiom for collecting *all* violations for a document (as opposed to `jsonschema.validate()`, which only raises on the first error and is unsuitable for "log every invalid field" requirements).
- `error.path` gives the exact field path; `error.message` gives a (generic, auto-generated) description — this would need a translation layer to produce the PRD's "exact rule violation" wording if adopted.
- `jsonschema.exceptions.ErrorTree` is available for grouping errors by field programmatically, if nested/multi-error-per-field reporting were ever needed — not required for the current field-level-only MVP scope.

This confirms `jsonschema` is a technically sound, current fallback — but its API still requires a message-mapping layer, which narrows its advantage over a hand-rolled evaluator for a four-rule-type MVP.

## Code References

- `dataguard/models.py:6-9` - `Outcome` enum (`VALID`, `INVALID`, `ERRORED`)
- `dataguard/models.py:12-16` - `RecordResult` dataclass (`row_id`, `outcome`, `reason`)
- `dataguard/sync.py:45` - `_classify` invocation site, one call per extracted row
- `dataguard/sync.py:47-48` - split of results into `invalid`/`valid` lists that drives write order
- `dataguard/sync.py:116-120` - `_load_rules`, parses rules JSON with no schema validation of its own
- `dataguard/sync.py:123-124` - `_classify` stub, currently always returns `Outcome.VALID`
- `rules/orders.json` - empty placeholder (`{}`)
- `rules/.gitkeep` - pointer comment to PRD § Business Logic (schema itself not defined there)
- `context/foundation/prd.md:64-65` - FR-002, scopes rules to null/type/comparison/regex, field-level only
- `context/foundation/prd.md:100-104` - § Business Logic, three-outcome contract and write-order rule
- `context/foundation/roadmap.md:126` - Open Question #2, rules JSON schema is an explicit S-02 blocker
- `pyproject.toml` - current dependency list; no validation library present
- `tests/test_sync.py` - no existing `_classify` tests; greenfield

## Architecture Insights

- The codebase consistently keeps `sync.py`'s stubs as pure, narrowly-scoped functions with a single typed input/output contract (see `_extract`, `_load_rules`) — `_classify(row, rules) -> RecordResult` should follow the same shape: no hidden state, no I/O, deterministic given `row` and `rules`.
- `RecordResult.reason` is a plain `str | None` — the classification layer owns turning any validation error (whether from a hand-rolled evaluator or a library) into one final string. This argues for keeping the rule-evaluation logic itself decoupled from `RecordResult` construction, so message formatting stays in one place regardless of which rule types get added later.
- AGENTS.md's "no field-value leakage" hard rule applies here too: any reason string produced by `_classify` must describe the *rule that failed* (e.g., `"age: must be >= 0"`), never echo the offending field's raw value into console output — only the Supabase rejection log (a future S-03 concern) is allowed to carry actual field values.
- The project's broader convention (CLAUDE.md: "don't add abstractions beyond what the task requires") plus the closed, four-rule-type MVP scope in FR-002 together argue for the smallest correct implementation — a stdlib rule evaluator — over introducing a general-purpose validation library whose extra capabilities (nested schemas, composite `anyOf`/`oneOf` rules, coercion) aren't needed until cross-field rules are in scope (deferred to v2 per PRD § Non-Goals).

## Historical Context (from prior changes)

- `context/archive/2026-06-05-source-db-extraction/` (S-01) established `_extract` and `_connect_source`, leaving `_classify`, `_connect_target`, `_write_rejections_to_supabase`, and `_commit_valid` as the remaining stubs. No prior decision touched the rules format — this is the first change to address it.
- `context/foundation/lessons.md` has one entry (Rich `Console(no_color=...)` convention) — not directly relevant to rule evaluation, but any new module (e.g., a `dataguard/rules.py` if the evaluator is split out) must still follow it if it prints anything.

## Related Research

- None yet under `context/changes/**/research.md` or `context/archive/**/research.md` — this is the first research artifact for S-02.

## Open Questions

1. **Exact rules JSON shape**: this research recommends the four rule types (null/required, type, comparison, regex) but does not yet lock a concrete JSON key/value syntax (e.g., `{"field": "age", "rule": "gte", "value": 0}` vs. a nested-per-field dict like Cerberus's). That syntax decision belongs in `/10x-plan`, informed by this research.
2. **Errored vs. invalid boundary**: the PRD distinguishes "invalid" (fails a rule) from "errored" (unparseable/malformed) but `_classify` currently receives already-parsed dict rows from `_extract` — need to confirm in planning what would actually trigger `Outcome.ERRORED` at the `_classify` layer (e.g., a field missing entirely vs. present-but-wrong-type) versus rules-file-level errors already handled by `_load_rules`.
3. **Rules-file self-validation**: should the rules file itself be validated against a meta-schema at load time (to catch a malformed rules file early, distinct from `_load_rules`'s current JSON-syntax-only check)? Not required by FR-002 but worth a planning-time decision.
