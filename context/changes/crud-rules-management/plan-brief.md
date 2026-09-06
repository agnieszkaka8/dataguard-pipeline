# CRUD Rules Management — Plan Brief

> Full plan: `context/changes/crud-rules-management/plan.md`
> Research: `context/changes/crud-rules-management/research.md`

## What & Why

Today, validation rules are a single local JSON file, hand-edited and passed via
`--rules <path>` on every `dataguard sync` invocation — no name, no catalog, no way
to manage them without editing files directly. This change adds named, Supabase-
backed rule sets with full CRUD (`dataguard rules list|get|create|update|delete`) and
lets `sync` fetch one by name (`--rule-set <name>`) instead of a file path.

## Starting Point

`dataguard/rules.py` already defines a tested JSON schema and evaluator
(`{"rules": [{"field","check","value"}]}`); `dataguard/sync.py:_load_rules` reads and
validates a local file. This codebase has never read structured data back from
Supabase — the only table interaction is one write (the rejections log). The
per-engineer Supabase Auth session from `auth-supabase-login` exists but is
completely disconnected from Supabase table access, which is always done via the
static service-role key.

## Desired End State

An engineer runs `dataguard rules create orders-v2 --table orders --from-file
rules/orders.json` once, then `dataguard sync --table orders --rule-set orders-v2`
on every subsequent run — no local file needed at run time. `dataguard rules list`
shows every stored rule set; `update`/`delete` manage them going forward. The
existing `--rules <path>` flow is untouched and keeps working exactly as before.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Credentials for CRUD | Service-role key (not the engineer's own session) | Matches the one existing Supabase table pattern and the project's anti-premature-abstraction bias; per-engineer attribution stays a deferred follow-up, consistent with how `auth-supabase-login` treated the same tradeoff | Plan |
| Local files vs. Supabase | Coexist — `--rules` and `--rule-set` are mutually exclusive options on `sync` | Zero disruption to existing tests and the CI workflow that already invokes `--rules` | Plan |
| Rule-set scope | One table per rule set (`table_name` column), cross-checked against `--table` at sync time | Matches today's one-file-per-table mental model; prevents applying the wrong table's rules | Plan |
| CLI shape | New `dataguard rules` subcommand group | Matches Typer's resource-CRUD pattern; keeps `sync` focused on its own job | Plan |
| Authoring | `--from-file <path>` upload only, reusing `validate_rules_schema` | Zero new rule format to design or test; engineers keep authoring the JSON they already know | Plan |
| Fetch errors | Hard error, non-zero exit, no fallback to a local file | Matches AGENTS.md's explicit no-silent-failure hard rule | Plan |
| Concurrency | Last-write-wins, `updated_at` for visibility only | Matches the project's small-team, single-org-trust posture | Plan |
| Access control | Any authenticated engineer can manage any rule set — no ownership/RLS | No role system exists anywhere in the project yet; adding one is out of scope here | Plan |
| Testing | Mock-only unit tests (no live Supabase project required) | Matches 100% of existing Supabase-related tests in this repo (`test_auth.py`) | Plan |
| Not-found / fetch semantics | Postgrest `.maybe_single()` (not `.single()`) so a miss returns `data=None` | Lets the module produce its own consistent "not found" message instead of parsing SDK errors | Plan |

## Scope

**In scope:**
- `supabase/rule_sets.sql` schema (service-role only, no RLS)
- `dataguard/rule_sets.py`: list/get/create/update/delete against Supabase
- `dataguard rules list|get|create|update|delete` CLI commands
- `sync --rule-set <name>`, mutually exclusive with `--rules`, with a table-scope cross-check

**Out of scope:**
- Row Level Security / per-engineer permissions
- Optimistic concurrency or version history
- Interactive rule-authoring UI
- Replacing `--rules <path>`
- Cross-table rule sets or a rename operation
- Attaching engineer identity to rule-set rows (mirrors the same deferral already made for the rejections log)

## Architecture / Approach

`dataguard/rule_sets.py` owns all Supabase table I/O for this resource, mirroring how
`watermark.py` and `auth.py` each own one piece of state. `dataguard/rules.py` keeps
its existing job (evaluate + structurally validate a rules list) and is reused by
both the new module (validate before writing) and `sync.py` (re-validate a fetched
rule set defensively). `run_sync`'s signature is extended additively — `rules_path`
becomes optional, a new optional `rule_set_name` is added — so every existing caller
and test keeps working unchanged.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Data model & CRUD module | `rule_sets.sql`, `RuleSet` model, `dataguard/rule_sets.py` | Getting Postgrest's not-found/conflict semantics right without a live project to test against |
| 2. CLI integration | `dataguard rules ...` commands, `sync --rule-set` + table cross-check | The mutual-exclusivity guard and table-mismatch check — get either wrong and rules silently apply to the wrong table |
| 3. Testing & leakage sweep | Unit tests (mocked), extended no-leakage sweep | Ensuring mocks accurately reflect real Postgrest response shapes |

**Prerequisites:** A Supabase project with `SUPABASE_URL`/`SUPABASE_KEY` already
configured (already required by the existing `auth-supabase-login` and
`live-sync-write-cycle` changes); `supabase/rule_sets.sql` applied before manual
verification.
**Estimated effort:** ~3 sessions across 3 phases.

## Open Risks & Assumptions

- The exact Postgrest response shape (`APIResponse.data`, `APIError.code`) was
  confirmed against the installed `postgrest` package version, not live-tested
  against a real Supabase project — the implementer should do one real round-trip
  during Phase 1/2 manual verification to catch any drift.
- This change does not deliver per-engineer attribution or access control for rule
  sets, matching the same deferral already made for the rejections log in
  `auth-supabase-login` — both remain open follow-ups if per-user accountability
  becomes a real requirement later.

## Success Criteria (Summary)

- An engineer can create, list, inspect, update, and delete a named rule set entirely
  through the CLI, without hand-editing a file at its storage location.
- `dataguard sync --rule-set <name>` behaves identically to the equivalent
  `--rules <path>` run for validation and write-order purposes.
- Every error path (not found, already exists, wrong table, malformed upload) fails
  with a clear message and non-zero exit — never a silent fallback or raw Supabase
  error.
