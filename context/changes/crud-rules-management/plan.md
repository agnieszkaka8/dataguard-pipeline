# CRUD Rules Management Implementation Plan

## Overview

Add CRUD management of named, table-scoped validation rule sets stored in Supabase,
alongside the existing local-file `--rules <path>` workflow. `dataguard sync` gains a
`--rule-set <name>` option (mutually exclusive with `--rules`) to fetch a rule set by
name instead of requiring a JSON file on disk, and a new `dataguard rules` command
group lets engineers list, inspect, create, update, and delete named rule sets without
hand-editing files at their storage location.

## Current State Analysis

- Rules today are a pure value, not a resource: `dataguard/cli.py:52` requires
  `--rules <path>` (a `Path`), `dataguard/cli.py:66-68` checks the file exists, and
  `dataguard/sync.py:127-132` (`_load_rules`) parses JSON and delegates to
  `dataguard/rules.py:111-162` (`validate_rules_schema`). No name, catalog, or
  persistence beyond the filesystem path exists.
- The rules JSON shape — `{"rules": [{"field", "check", "value"}]}`, checks
  `required|type|gte|lte|gt|lt|eq|ne|regex` — was a deliberate, tested design
  (`dataguard/rules.py:12-18,77-87`); this plan reuses it unchanged, both as the
  `--from-file` upload format and as the shape re-validated on every read.
- This codebase has never read structured data back from Supabase. The only existing
  table interaction is one write: `dataguard/sync.py:175`
  (`client.table("rejections").insert(rows).execute()`), authenticated with the
  static service-role `SUPABASE_KEY` (`.env.example:14`). `dataguard/auth.py:90-96`
  builds a separate client for the same key, used only for Supabase Auth endpoints —
  the per-engineer login session is never attached to table access.
- `supabase/rejections.sql` (9 lines) is the only existing precedent for how a
  Supabase table's schema is defined in this repo: a plain root-level `.sql` file, no
  migrations directory, no RLS.
- `dataguard/cli.py:25-39` (`_root` callback) already gates every command except
  `logout` behind `env_check()` + `auth.ensure_session()` — a new `rules` subcommand
  group is automatically covered by this gate with no changes needed there.

## Desired End State

An engineer can run `dataguard rules create orders-v2 --table orders --from-file
rules/orders.json` to store a named rule set in Supabase; `dataguard rules list` shows
every stored rule set with its table and last-updated time; `dataguard rules get
orders-v2` prints its contents; `dataguard rules update orders-v2 --from-file
rules/orders-v2.json` overwrites its contents; `dataguard rules delete orders-v2`
removes it (with a confirmation prompt). `dataguard sync --table orders --rule-set
orders-v2` runs a sync using that stored rule set instead of a local file — behaving
identically to `--rules <path>` from the validation/write-order perspective. The
existing `--rules <path>` flow is untouched and continues to work exactly as before.

Verify by: creating a rule set from `rules/orders.json`, listing it, fetching it by
name via `sync --rule-set`, updating it, and deleting it — each step producing the
same classification/console behavior as the equivalent local-file run, and each error
path (not found, already exists, wrong table, malformed upload) producing a clear
message and non-zero exit.

### Key Discoveries:

- `dataguard/cli.py:25-39` — the auth gate already covers any new subcommand
  automatically; no changes needed to `_root` for the new `rules` group.
- Postgrest's `.select(...).eq("name", name).maybe_single().execute()` returns
  `response.data is None` for zero matches, rather than raising — the deliberate
  choice for "fetch by name," so the module can produce its own consistent
  "not found" message instead of parsing SDK-internal error codes.
- Postgrest surfaces a Postgres unique-constraint violation as
  `postgrest.exceptions.APIError` with `.code == "23505"` — this is how `create`
  distinguishes "name already exists" from other insert failures.
- `.update(...)` / `.delete()` return the affected rows by default
  (`returning=representation`); an empty `response.data` list after `.eq("name",
  name)` is how `update`/`delete` detect "not found" without a prior read.
- `dataguard/sync.py`'s `run_sync` is the existing, already-tested orchestrator
  (`tests/test_sync.py`) — this plan extends its signature additively
  (`rules_path` becomes optional, a new optional `rule_set_name` is added) rather
  than replacing it, so every existing call site and test that passes `rules_path`
  keeps working unchanged.

## What We're NOT Doing

- No Row Level Security / per-engineer database permissions. All rule-set CRUD uses
  the existing service-role key, matching the one existing Supabase table precedent;
  any authenticated engineer can create/update/delete any rule set (no ownership,
  roles, or RLS policies).
- No optimistic concurrency or version history. Updates are last-write-wins;
  `updated_at` is recorded for visibility only, not conflict detection.
- No interactive rule-authoring UI. `create`/`update` only accept `--from-file
  <path>`, reusing the existing local JSON format and `validate_rules_schema`.
- No replacement of `--rules <path>`. Local files and named Supabase rule sets
  coexist as two mutually exclusive ways to supply rules to `sync`.
- No cross-table rule sets. Each named rule set is scoped to exactly one table
  (`table_name` column); `sync` hard-errors if `--table` doesn't match the rule
  set's stored table.
- No rename operation. `update` overwrites a rule set's `rules` content only; there
  is no way to change a rule set's `name` or `table_name` after creation (delete and
  recreate instead).
- No audit trail, diff, or history beyond the single `updated_at` timestamp.

## Implementation Approach

A new `dataguard/rule_sets.py` module owns all Supabase table I/O for rule sets
(list/get/create/update/delete), mirroring how `dataguard/watermark.py` and
`dataguard/auth.py` each own one piece of local/remote state — `dataguard/rules.py`
keeps its existing, unrelated job (evaluating and structurally validating a rules
list) and is called *by* `rule_sets.py` (to validate before writing) and by `sync.py`
(to validate a fetched rule set defensively before use), never duplicated. CLI wiring
is a new Typer sub-app (`dataguard rules ...`) plus one new option on the existing
`sync` command; the auth/env gate already in `_root` requires no changes.

## Critical Implementation Details

### Postgrest fetch/mutation semantics are not obvious from the file paths alone

`get_rule_set` must use `.maybe_single()` (not `.single()`) so a missing name returns
`data=None` instead of raising a Postgrest-internal error — the module raises its own
`RuntimeError("Rule set 'X' not found")` from that `None` check. `create_rule_set`
must catch `postgrest.exceptions.APIError` and check `exc.code == "23505"`
specifically to distinguish "name already exists" from any other insert failure (e.g.
network) — a generic `except Exception` would conflate the two into the same
unhelpful message. `update_rule_set`/`delete_rule_set` detect "not found" from an
empty `response.data` list after the `.eq("name", name)` filter, not from an
exception — Postgrest does not raise when an update/delete filter matches zero rows.

### `run_sync`'s rules source is resolved once, with a table cross-check, before validation reuse

`_resolve_rules(table, rules_path, rule_set_name)` in `sync.py` is the single place
that decides which source to use and enforces that a fetched rule set's stored
`table_name` matches the `--table` being synced — this check must happen *before*
`validate_rules_schema` re-validation, so a wrong-table rule set fails with a clear
"scoped to table 'Y', not 'Z'" message rather than an unrelated classification error
later in the run.

## Phase 1: Data model & Supabase CRUD module

### Overview

Define the `rule_sets` table schema, add a `RuleSet` model, and build
`dataguard/rule_sets.py` with list/get/create/update/delete functions against a
mocked Supabase client. No CLI wiring in this phase.

### Changes Required:

#### 1. Supabase schema

**File**: `supabase/rule_sets.sql` (new)

**Intent**: Define the `rule_sets` table, following the only existing precedent
(`supabase/rejections.sql`) — a plain checked-in `.sql` file, no migrations tooling.

**Contract**: Columns `id` (identity PK), `name` (`TEXT NOT NULL UNIQUE`),
`table_name` (`TEXT NOT NULL`), `rules` (`JSONB NOT NULL`, storing the bare rules
list — not the `{"rules": [...]}` file-wrapper form), `created_at`/`updated_at`
(`TIMESTAMPTZ NOT NULL DEFAULT now()`). No RLS statement, consistent with
`rejections.sql` and the service-role-only access decision.

#### 2. RuleSet model

**File**: `dataguard/models.py`

**Intent**: Add a `RuleSet` dataclass representing a stored rule set, following the
existing `RecordResult`/`Session` dataclass convention.

**Contract**: `RuleSet` carries `name: str`, `table_name: str`,
`rules: list[dict[str, Any]]`, `updated_at: str`.

#### 3. Rule-set CRUD module

**File**: `dataguard/rule_sets.py` (new)

**Intent**: Own all Supabase table I/O for named rule sets, so neither `cli.py` nor
`sync.py` talks to the Supabase table API directly for this resource.

**Contract**: Expose `list_rule_sets() -> list[RuleSet]`; `get_rule_set(name: str) ->
RuleSet` (raises `RuntimeError` if not found, per the `.maybe_single()` pattern in
Critical Implementation Details); `create_rule_set(name: str, table_name: str, rules:
list[dict[str, Any]]) -> None` (validates via `validate_rules_schema` before
inserting; raises `RuntimeError` with an "already exists" message on `APIError.code
== "23505"`, and a generic write-failure `RuntimeError` for anything else — same
sanitizing-wrapper discipline as `sync.py:_write_rejections_to_supabase`, never
surfacing raw Supabase/connection details); `update_rule_set(name: str, rules:
list[dict[str, Any]]) -> None` (validates, then raises `RuntimeError` if the update
affects zero rows); `delete_rule_set(name: str) -> None` (raises `RuntimeError` if
the delete affects zero rows). All functions construct their Supabase client via the
same service-role `create_client(SUPABASE_URL, SUPABASE_KEY)` pattern as
`sync.py:_connect_supabase`.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_rule_sets.py -v`
- Type checking passes: `uv run mypy dataguard/`
- Linting passes: `uv run ruff check dataguard/`

#### Manual Verification:

- `supabase/rule_sets.sql` applied to a real Supabase project creates the table with
  the expected columns and a unique constraint on `name`

---

## Phase 2: CLI integration

### Overview

Add the `dataguard rules list|get|create|update|delete` subcommand group, and give
`sync` a `--rule-set <name>` option mutually exclusive with `--rules`, including the
table-scope cross-check.

### Changes Required:

#### 1. `rules` subcommand group

**File**: `dataguard/cli.py`

**Intent**: Expose rule-set CRUD as CLI commands, reusing `sync._load_rules`'s
JSON-parsing + schema-validation for `--from-file` uploads so authoring stays
identical to the existing local-file format.

**Contract**: A new Typer sub-app (`rules_app = typer.Typer()`,
`app.add_typer(rules_app, name="rules")`) with five commands: `list` (prints a Rich
Table — Name / Table / Last Updated — or a friendly "No rule sets found" message
when empty); `get <name>` (pretty-prints the rule set's JSON rules); `create <name>
--table <table> --from-file <path>` (loads+validates the file via the existing
`_load_rules`, then calls `rule_sets.create_rule_set`); `update <name> --from-file
<path>` (same loading, calls `rule_sets.update_rule_set`); `delete <name>` (prompts
`typer.confirm(...)` before calling `rule_sets.delete_rule_set`, mirroring the
existing risky-action confirmation pattern in `cli.py:_guard_since_override`). Every
`RuntimeError` from `rule_sets.py` is caught and printed via the module's `console`,
exiting via `typer.Exit(code=1)` — the same convention as every other CLI failure
path.

#### 2. `sync --rule-set` option

**File**: `dataguard/cli.py`

**Intent**: Let `sync` source its rules from a named Supabase rule set instead of a
local file.

**Contract**: `--rules` becomes `Optional[Path]` (default `None`, no longer
required); a new `--rule-set` `Optional[str]` (default `None`) is added. Before
calling `run_sync`, `sync()` asserts exactly one of the two is provided (`typer.Exit
(code=1)` with a clear message otherwise) and only checks `rules.exists()` when
`--rules` was the one given.

#### 3. `run_sync` rules-source resolution

**File**: `dataguard/sync.py`

**Intent**: Let `run_sync` accept either source without `cli.py` pre-loading rules
itself, and enforce the table-scope cross-check in one place.

**Contract**: `run_sync`'s `rules_path: Path` parameter becomes `rules_path: Path |
None = None`, plus a new `rule_set_name: str | None = None`. A new private
`_resolve_rules(table, rules_path, rule_set_name)` performs the source dispatch: if
`rules_path` is given, behaves exactly as today (`_load_rules(rules_path)`);
otherwise fetches via `rule_sets.get_rule_set(rule_set_name)`, raises `RuntimeError`
if `rule_set.table_name != table` (the cross-check from Critical Implementation
Details), and re-validates `rule_set.rules` through `validate_rules_schema` before
use. `run_sync` calls `_resolve_rules(...)` where it currently calls `_load_rules
(rules_path)` directly.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_cli.py tests/test_sync.py -v`
- Type checking passes: `uv run mypy dataguard/`
- Linting passes: `uv run ruff check dataguard/`

#### Manual Verification:

- `dataguard rules create orders-v2 --table orders --from-file rules/orders.json`
  followed by `dataguard rules list` shows the new rule set
- `dataguard sync --table orders --rule-set orders-v2 --dry-run` runs identically to
  the equivalent `--rules rules/orders.json --dry-run` invocation
- `dataguard sync --table orders --rule-set orders-v2 --rules rules/orders.json
  --dry-run` (both flags) exits non-zero with a clear "exactly one of" message
- `dataguard sync --table users --rule-set orders-v2 --dry-run` (wrong table) exits
  non-zero with the table-mismatch message
- `dataguard rules delete orders-v2` prompts for confirmation before deleting

---

## Phase 3: Testing & leakage sweep

### Overview

Cover `dataguard/rule_sets.py` and the new CLI surface with unit tests mocking the
Supabase client, and extend the existing no-leakage regression sweep to include
`dataguard.rule_sets`' console output.

### Changes Required:

#### 1. Rule-set module unit tests

**File**: `tests/test_rule_sets.py` (new)

**Intent**: Test `list_rule_sets`, `get_rule_set`, `create_rule_set`,
`update_rule_set`, `delete_rule_set` in isolation, mirroring `tests/test_auth.py`'s
mocked-client style (no live Supabase project).

**Contract**: Monkeypatch the module's Supabase client construction with a
`MagicMock` whose `.table("rule_sets")...execute()` chain returns canned
`APIResponse`-shaped objects (`SimpleNamespace(data=...)`). Cover: `get_rule_set`
found / not-found (`data=None`); `create_rule_set` success, already-exists
(`APIError` with `code="23505"`), and a generic failure wrapped into `RuntimeError`
without leaking Supabase internals; `update_rule_set`/`delete_rule_set` success and
not-found (empty `data` list); `create_rule_set`/`update_rule_set` reject an invalid
`rules` payload via `validate_rules_schema` before ever calling Supabase.

#### 2. CLI and sync integration tests

**File**: `tests/test_cli.py`, `tests/test_sync.py`

**Intent**: Cover the new `rules` subcommands and `sync`'s `--rule-set` path at the
CLI/orchestrator level, following the existing `unittest.mock.patch` conventions in
each file.

**Contract**: `test_cli.py` — each `rules` subcommand's happy path and its
`RuntimeError` → `typer.Exit(code=1)` translation; the exactly-one-of
`--rules`/`--rule-set` guard (neither, both, each alone); `delete`'s confirmation
prompt. `test_sync.py` — `_resolve_rules` behavior: local-file path unchanged
(regression), Supabase-fetch path, table-mismatch hard error, and re-validation of a
malformed fetched rule set producing the same schema-error message as a malformed
local file.

#### 3. No-leakage sweep extension

**File**: `tests/test_no_leakage_regression.py`

**Intent**: Extend the existing generic console-output sweep to the new module,
consistent with how it already covers `cli.py`/`sync.py`/`watermark.py`/`auth.py`.

**Contract**: Add `monkeypatch.setattr("dataguard.rule_sets.console.print", _record)`
to `_capture_console_prints`; no new dedicated leakage test cases are required since
rule-set names/contents aren't credentials, but this keeps the sweep's stated
"any future console.print added anywhere in those modules is automatically covered"
guarantee accurate now that a fifth module exists.

### Success Criteria:

#### Automated Verification:

- Full test suite passes: `uv run pytest -m "not integration"`
- Type checking passes: `uv run mypy dataguard/`
- Linting passes: `uv run ruff check dataguard/`

#### Manual Verification:

- Reading through `tests/test_rule_sets.py` confirms no test hits a real Supabase
  project — every network call is mocked

---

## Testing Strategy

### Unit Tests:

- Rule-set module: found/not-found fetch, create success/conflict/failure,
  update/delete success/not-found, schema-validation rejection before any Supabase
  call
- CLI: each `rules` subcommand's success and error paths, the mutual-exclusivity
  guard on `sync`, the delete confirmation prompt
- Sync: `_resolve_rules`'s local-file regression path, Supabase-fetch path, and
  table-mismatch hard error

### Integration Tests:

- None required — all Supabase calls are mocked. If a future need arises for a live
  round-trip check, use the existing `integration` pytest marker, matching
  `tests/test_sync.py::test_extract_real_db`'s pattern.

### Manual Testing Steps:

1. Apply `supabase/rule_sets.sql` to a real Supabase project.
2. `dataguard rules create orders-v2 --table orders --from-file rules/orders.json`,
   then `dataguard rules list` and `dataguard rules get orders-v2` — confirm the
   content round-trips.
3. `dataguard sync --table orders --rule-set orders-v2 --dry-run` — confirm identical
   behavior to the equivalent `--rules` run.
4. `dataguard rules update orders-v2 --from-file <a modified rules file>` — confirm
   the change is reflected in a subsequent `sync --rule-set orders-v2`.
5. `dataguard rules delete orders-v2` — confirm the confirmation prompt, then confirm
   deletion; a subsequent `sync --rule-set orders-v2` fails with "not found."
6. Attempt `dataguard rules create orders-v2 ...` twice with the same name — confirm
   the second attempt fails with an "already exists" message, not a raw Postgres
   error.

## Performance Considerations

One additional Supabase round-trip per `sync --rule-set` invocation (the fetch) —
negligible against the 60s/10k-record throughput NFR, and no worse than the existing
one-write-per-run pattern for rejections.

## Migration Notes

No existing data to migrate — `rule_sets` is a new, empty table. `rules/orders.json`
can optionally be uploaded as a first rule set via `dataguard rules create` during
manual verification, but nothing requires it; the local file continues to work
standalone.

## References

- Research: `context/changes/crud-rules-management/research.md`
- Rules schema/validation precedent: `dataguard/rules.py:12-18,77-87,90-101,111-162`
- Local-file loading precedent: `dataguard/sync.py:127-132` (`_load_rules`)
- Supabase write/client precedent: `dataguard/sync.py:151-179`
- Supabase Auth client construction precedent: `dataguard/auth.py:90-96`
- Only existing Supabase schema file: `supabase/rejections.sql`
- CLI auth/env gate (already covers new commands): `dataguard/cli.py:25-39`
- Risky-action confirmation precedent: `dataguard/cli.py:_guard_since_override`
- Mock-based Supabase test precedent: `tests/test_auth.py`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Data model & Supabase CRUD module

#### Automated

- [ ] 1.1 Unit tests pass: `uv run pytest tests/test_rule_sets.py -v`
- [x] 1.2 Type checking passes: `uv run mypy dataguard/` — 15e117a
- [x] 1.3 Linting passes: `uv run ruff check dataguard/` — 15e117a

#### Manual

- [ ] 1.4 `supabase/rule_sets.sql` applied to a real Supabase project creates the table with the expected columns and a unique constraint on `name`

### Phase 2: CLI integration

#### Automated

- [x] 2.1 Unit tests pass: `uv run pytest tests/test_cli.py tests/test_sync.py -v`
- [x] 2.2 Type checking passes: `uv run mypy dataguard/`
- [x] 2.3 Linting passes: `uv run ruff check dataguard/`

#### Manual

- [ ] 2.4 `dataguard rules create orders-v2 --table orders --from-file rules/orders.json` followed by `dataguard rules list` shows the new rule set
- [ ] 2.5 `dataguard sync --table orders --rule-set orders-v2 --dry-run` runs identically to the equivalent `--rules rules/orders.json --dry-run` invocation
- [ ] 2.6 Both `--rule-set` and `--rules` together exits non-zero with a clear "exactly one of" message
- [ ] 2.7 Wrong-table `--rule-set` exits non-zero with the table-mismatch message
- [ ] 2.8 `dataguard rules delete orders-v2` prompts for confirmation before deleting

### Phase 3: Testing & leakage sweep

#### Automated

- [ ] 3.1 Full test suite passes: `uv run pytest -m "not integration"`
- [ ] 3.2 Type checking passes: `uv run mypy dataguard/`
- [ ] 3.3 Linting passes: `uv run ruff check dataguard/`

#### Manual

- [ ] 3.4 Reading through `tests/test_rule_sets.py` confirms no test hits a real Supabase project
