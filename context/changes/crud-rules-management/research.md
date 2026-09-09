---
date: 2026-09-06T20:09:12+02:00
researcher: Aga
git_commit: 8040c7cd9ab5e81856468bc2916790eac3dfef49
branch: main
repository: dataguard-pipeline
topic: "CRUD management of named validation rule sets stored in Supabase"
tags: [research, codebase, rules, supabase, auth, cli]
status: complete
last_updated: 2026-09-06
last_updated_by: Aga
---

# Research: CRUD management of named validation rule sets stored in Supabase

**Date**: 2026-09-06T20:09:12+02:00
**Researcher**: Aga
**Git Commit**: 8040c7cd9ab5e81856468bc2916790eac3dfef49
**Branch**: main
**Repository**: dataguard-pipeline

## Research Question

What does CRUD for rules management mean in this project? Investigate how validation rules
are currently loaded (rules.py, cli.py), where they live (local JSON file), and what it would
take to let users create, read, update, and delete named rule sets stored in Supabase — so the
CLI can list available rule sets, fetch one by name instead of `--rules <path>`, and let
engineers manage them without editing JSON files by hand.

## Summary

Today, "rules" are a single local JSON file, hand-edited, passed per-invocation via
`--rules <path>`, with no concept of a name, a catalog, or persistence beyond the filesystem.
There is no code anywhere in the repo that reads structured data back out of Supabase — the
only existing Supabase table interaction is a one-way write (`rejections` log). Making rule
sets Supabase-backed and named is a genuinely new capability, not an extension of an existing
pattern, and it intersects with the just-shipped `auth-supabase-login` change in a way that
isn't yet wired up: the per-engineer login session and the Supabase client used for all table
I/O are currently completely disconnected (both today's write path and any future rules-read
path default to the same static service-role key, ignoring who is actually logged in).

## Detailed Findings

### How rules are loaded and validated today

- `dataguard/cli.py:52` — the `sync` command's `--rules` option is a `Path`, required
  (`typer.Option(..., "--rules", "-r", ...)`); `cli.py:66-68` checks `rules.exists()` and exits
  non-zero with `"Rules file not found: {rules}"` if missing.
- `dataguard/sync.py:127-132` — `_load_rules(path)` reads the file with `path.read_text()`,
  `json.loads(...)`, catches `json.JSONDecodeError` → `RuntimeError("Rules file is not valid
  JSON")`, then delegates structural validation to `validate_rules_schema`.
- `dataguard/rules.py:111-162` — `validate_rules_schema(rules_data)` enforces the shape
  `{"rules": [{"field": str, "check": str, "value": <check-dependent>}]}`. Checks supported:
  `required, type, gte, lte, gt, lt, eq, ne, regex` (`rules.py:77-87`), with `type`'s value
  constrained to `str|int|float|bool|number` (`rules.py:12-18`). Unknown `check`/`type` values
  get a `difflib`-based "did you mean" suggestion (`rules.py:104-108`).
- `dataguard/rules.py:90-101` — `evaluate(row, rules)` runs rules **in file order** and returns
  on the **first failing rule** (no aggregation) — this ordering is a locked design decision
  (see Historical Context), not an incidental implementation detail.
- The only rules file that exists is `rules/orders.json` (10 rules covering `id`,
  `customer_email`, `amount`, `status`); `rules/.gitkeep` documents the convention ("Validation
  rule files go here") and points at `context/foundation/prd.md` § Business Logic for the
  schema — that pointer is **stale**: the PRD's Business Logic section (prd.md:96-104) defines
  the three-outcome classification model but says nothing about JSON structure. The actual
  schema exists only in code and its tests.

### Where rules "live" — no name, no catalog, no persistence beyond a file path

There is currently no concept of a rule-set **name** anywhere in the system. A rules file is
identified purely by its filesystem path, supplied fresh on every invocation. There is no
listing mechanism, no versioning, no "current" or "default" rule set, and no metadata (who
wrote it, when, for which table) beyond what a `git log` on the file itself would tell you.
Nothing about the current design assumes more than one rules file per table, or reuse of a
rules file across runs beyond the operator remembering its path.

### No existing pattern for reading structured data back from Supabase

- Repo-wide search for `.select(`, `.from_(`, `.table(` found exactly **one** table call site:
  `dataguard/sync.py:175` — `client.table("rejections").insert(rows).execute()`, a write. There
  is no `.select()` anywhere in the codebase. **A "fetch rule set by name" read path would be
  the first Supabase read the codebase has ever done.**
- `dataguard/sync.py:151-157` (`_connect_supabase`) and `dataguard/auth.py:90-96` (`_client`)
  both call `create_client(url, key)` with the **same static `SUPABASE_KEY`** from `.env`,
  documented as the **service-role key** (`.env.example:14`, `"Supabase service-role key
  (Settings → API → service_role — keep secret)"`). Every Supabase table operation in this
  codebase — today's one write, and any future rule-set CRUD — runs with full service-role
  privileges, bypassing Postgres Row Level Security entirely.

### The per-engineer auth session is disconnected from Supabase table access

This is the most significant architectural finding for this change, because CRUD-by-engineer
is exactly the kind of feature where "which engineer" should matter:

- `dataguard/auth.py:31-53` (`ensure_session`) produces a `Session` (`dataguard/models.py`)
  carrying the logged-in engineer's own `access_token`/`refresh_token` — this is a real,
  per-user Supabase Auth JWT.
- That token is **never** attached to any Supabase client used for table I/O. `_client()` in
  `auth.py:90-96` is used exclusively for `client.auth.sign_in_with_password` /
  `client.auth.refresh_session` — auth endpoints, not table access — and is discarded
  afterward. `sync.py`'s `_connect_supabase()` builds an entirely separate client, always from
  the static service-role key, with no reference to the logged-in session at all.
- Net effect: today, being logged in as a specific engineer has **zero bearing** on what that
  engineer can read or write in Supabase — the service-role key is the only credential that
  ever touches a table. Row Level Security policies scoped to `auth.uid()` would currently have
  no effect on any DataGuard code path, because no request is ever made with a user's own JWT.
- Confirmed via the previous change's own review: `context/archive/2026-07-24-live-sync-write-
  cycle/reviews/impl-review.md:93-94` explicitly notes the `rejections` table has "no RLS
  statement (acceptable if this table is only ever written with the service-role key)" and
  flags this as an unaddressed hardening item — i.e., the lack of RLS was a conscious,
  documented tradeoff for the write-only rejections case, not an oversight, but it was never
  revisited for a scenario (like rule-set CRUD) where per-user scoping might actually matter.

### The only existing Supabase schema precedent

`supabase/rejections.sql` (9 lines, full file) is the **only** existing example of how a
Supabase table's schema is defined and checked into this repo — a plain root-level `.sql`
file, not a migrations directory or ORM model:

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

No RLS policies, no indexes, no CHECK constraints. A `rule_sets` table would presumably follow
this same convention (a new `supabase/rule_sets.sql` file) rather than introducing a formal
migrations tool — there's no `supabase/migrations/` directory or CLI-managed migration history
anywhere in the repo.

### Test conventions a CRUD design should preserve

- `tests/test_validation.py` (321 lines) tests `evaluate`/`validate_rules_schema` directly with
  inline dict fixtures, no `pytest.mark.parametrize`, and exact-string error-message assertions
  (`pytest.raises(RuntimeError, match=...)`), including one-based rule indices and "did you
  mean" typo suggestions. Any new schema-validation entry point (e.g. validating a rule set
  fetched from Supabase) should produce the same error format, not a divergent one.
- `tests/test_sync.py:104-147` tests `_load_rules` specifically: valid file round-trip, invalid
  JSON, unknown-check schema error, missing `'rules'` key, and — notably —
  `test_load_rules_real_orders_json_is_valid` (line 142), a regression lock that loads the
  actual shipped `rules/orders.json` and asserts its field set. A Supabase-backed rules source
  would need an equivalent "real data round-trips" test, likely against a fixture/mock rule set
  rather than a live table.
- `context/foundation/test-plan.md:47,68,83` frames rules purely as a **classification-
  correctness risk** ("a record that should fail validation is classified VALID or vice versa")
  with a not-yet-started "Classification regression protection" phase — it has no awareness of
  rules as a storage/management concern. This document would need a new risk entry for the CRUD
  surface (e.g., "a corrupted or malicious rule set fetched from Supabase silently passes
  invalid records").

## Code References

- `dataguard/cli.py:52,66-68` — `--rules` option definition and missing-file check
- `dataguard/sync.py:127-132` — `_load_rules`: JSON parse + schema validation entry point
- `dataguard/sync.py:151-157` — `_connect_supabase`: service-role client construction (write path)
- `dataguard/sync.py:175` — the only existing Supabase table call in the repo (`insert`)
- `dataguard/rules.py:12-18,77-87,90-101,104-108,111-162` — check vocabulary, evaluation
  ordering (first-failure-wins), typo suggestions, schema validator
- `dataguard/auth.py:31-53,90-96` — `ensure_session`/`_client`: per-engineer session lifecycle,
  never connected to table-access clients
- `dataguard/models.py` — `Session` dataclass (access_token/refresh_token/expires_at)
- `rules/orders.json`, `rules/.gitkeep` — the only shipped rules file and its stale schema pointer
- `supabase/rejections.sql` — only existing Supabase schema-definition precedent
- `.env.example:14` — `SUPABASE_KEY` documented as the service-role key; no anon key used anywhere
- `tests/test_validation.py` (full file) — rules evaluator/schema-validator test conventions
- `tests/test_sync.py:104-147` — `_load_rules` test conventions, including the real-file
  regression lock
- `context/foundation/test-plan.md:47,68,83` — rules framed only as a correctness risk, not a
  storage concern
- `context/foundation/prd.md:96-104,124,128` — Business Logic section (no schema detail),
  Non-Goals (field-level only for MVP), Open Question #1 (cross-field rules deferred)

## Architecture Insights

- **Rules today are a pure value, not a resource.** There's no identity beyond a file path — no
  name, owner, version, or "current" pointer. Introducing named, stored rule sets is a genuine
  new domain concept, not a refactor of existing plumbing.
- **This codebase has never read from Supabase.** Every Supabase interaction so far is
  service-role-authenticated writes. A "fetch by name" read is architecturally new territory:
  first `.select()` call, first place client construction needs to decide *whose* credentials
  to use.
- **The auth session and table access are two unconnected subsystems.** `auth-supabase-login`
  built a real per-engineer identity (a Supabase Auth JWT, cached and refreshed), but nothing
  in the codebase uses that identity for anything beyond gating CLI invocation. Designing
  rule-set CRUD is a natural forcing function to decide: should rule-set reads/writes go through
  the engineer's own session (enabling RLS-based per-user access control, e.g. "only admins can
  delete rule sets") or continue through the service-role key (simpler, but no meaningful
  distinction between "logged in as engineer A" vs "logged in as engineer B" for anything other
  than audit-log identity)? This decision wasn't needed before because there was no read path
  and no per-user identity; both now exist, so it's a live design question this change must
  resolve rather than defer.
- **Schema-definition convention is a single checked-in `.sql` file per table**, not a
  migrations directory — `supabase/rejections.sql` is the only precedent. Consistency suggests
  `supabase/rule_sets.sql` for a new table rather than introducing new tooling.
- **Validation and error-message conventions are strict and tested to the exact string** —
  any new rule-set-fetch error path (not found, malformed, network failure) should follow the
  same "exact message + one-based indices + did-you-mean" style already established, to avoid
  the CLI feeling inconsistent between local-file and Supabase-backed rule sources.
- **CLI convention for listing named resources doesn't exist yet.** There is no precedent
  anywhere in `dataguard/cli.py` for a "list available X" command — `sync` and `logout` are the
  only two commands, both single-purpose. A `dataguard rules list` (or similar) command would be
  the first "enumerate a collection" UX in this CLI, no existing pattern to mirror beyond the
  general Typer/Rich conventions (Table for tabular output, per-module `Console` instance).

## Historical Context (from prior changes)

- `context/archive/2026-07-12-validation-engine/plan-brief.md` — the flat `{"rules": [...]}`
  JSON syntax was a deliberate choice ("composes multiple constraints per field naturally,
  trivial to test per-entry"), not an arbitrary default. Any Supabase-backed representation
  (e.g. a JSONB column) should preserve this shape rather than inventing a new one, to avoid
  bifurcating the schema between local-file and Supabase-backed rule sets.
- `context/archive/2026-07-12-validation-engine/research.md` — external rule-engine libraries
  (Cerberus, jsonschema, pydantic) were evaluated and explicitly rejected in favor of a
  hand-rolled stdlib evaluator, on both maintenance-risk and "smallest correct implementation"
  grounds (AGENTS.md/CLAUDE.md anti-premature-abstraction convention). This same bias should
  weigh against reaching for a heavier solution (e.g. a full ORM, a migrations framework, a
  rules-DSL) for the CRUD storage layer.
- `context/archive/2026-07-12-validation-engine/plan.md`'s "Migration Notes" section: "Not
  applicable" — confirms rules storage was never intended to be more than a local file at the
  time; no forward-looking design debt was left for this.
- `context/foundation/roadmap.md`'s Open Roadmap Question #2 ("Rules JSON schema — formal
  definition...") is technically still listed as open in the roadmap document itself, even
  though the schema was in fact defined and shipped in `validation-engine` — the roadmap was
  never edited to close it. Not blocking for this change, but worth a housekeeping note if
  `/10x-roadmap` is ever re-run.
- `context/archive/2026-07-24-live-sync-write-cycle/reviews/impl-review.md:93-94` — the explicit,
  documented decision to skip RLS on `rejections` because it's write-only via service-role key.
  This is the only prior discussion of RLS/access-control-at-the-table-level anywhere in the
  project's history, and it predates the existence of any per-engineer identity.

## Related Research

- `context/changes/auth-supabase-login/plan.md` and `plan-brief.md` — the per-engineer Supabase
  Auth login this change's design question (whose credentials read/write rule sets?) directly
  follows from.

## Open Questions

1. **Whose credentials should rule-set CRUD use — the engineer's own session or the service-
   role key?** This is the central architectural fork this research surfaced. Using the
   engineer's session enables real per-user access control via RLS (e.g., only certain
   engineers can delete a rule set) but requires wiring the `auth.py` session into a
   Supabase client for table access for the first time — a nontrivial, currently-nonexistent
   integration. Using the service-role key is simpler and consistent with today's only table
   write, but makes "logged in as engineer X" purely cosmetic for this feature.
2. **What does "named rule set" mean structurally?** Is a rule set scoped to one table (mirroring
   today's one-file-per-table convention, e.g. a `table_name` column) or can one named rule set
   be reused across tables? The current local-file model has no opinion here since a path is
   just a path.
3. **Local JSON files vs. Supabase-backed rule sets — coexist or replace?** Does `--rules <path>`
   stay as a fallback/offline option alongside a new `--rule-set <name>` flag, or does this
   change fully replace file-based rules? Affects both CLI surface and how much of
   `_load_rules`'s existing (well-tested) validation logic can be reused vs. needs a parallel
   path for Supabase-sourced data.
4. **Versioning and concurrent-edit behavior** — if two engineers edit the same named rule set,
   is there any conflict detection, version history, or is last-write-wins (consistent with the
   project's generally lightweight, single-org-trust posture per PRD §Access Control's original
   framing)?
5. **RLS policy design**, if the engineer's-own-session path is chosen: what distinguishes who
   can create/update/delete a rule set — is there any role concept beyond "logged in," or is
   any authenticated engineer equally privileged (mirroring the PRD's original "single access
   level" model, just now with individual login rather than a shared secret)?
