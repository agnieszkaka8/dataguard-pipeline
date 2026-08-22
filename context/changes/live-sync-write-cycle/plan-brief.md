# Live Sync Write Cycle (S-03) — Plan Brief

> Full plan: `context/changes/live-sync-write-cycle/plan.md`
> Research: `context/changes/roadmap/research.md`

## What & Why

S-03 is DataGuard's north star: the smallest end-to-end slice that proves the core hypothesis — a validation-first pipeline turns a silent transfer failure into an auditable, recoverable event. This plan fills the last three stub functions in `dataguard/sync.py` (`_connect_target`, `_write_rejections_to_supabase`, `_commit_valid`) and wires the already-implemented-but-unused `write_watermark()` into the success path.

## Starting Point

`run_sync`'s write-order orchestration (rejections before Target DB commit) is already correctly wired from S-01. `write_watermark()` exists and is unit-tested in isolation but has zero call sites. No Supabase client code exists anywhere. `RecordResult` (from S-02) carries only `row_id`/`outcome`/`reason` — no original field values, which both new write paths need.

## Desired End State

A live `dataguard sync` run against real Source/Target DBs and Supabase: invalid/errored records land in a Supabase `rejections` table with the exact rule violation and the full original record; valid records are inserted into Target DB; `.watermark` advances only after both writes succeed (or after rejection-logging alone, if there were zero valid records); any Supabase failure aborts before touching Target DB.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Rejection log schema | Flat typed columns (row_id, table_name, outcome, reason, raw_record jsonb, synced_at) | Queryable/filterable in Supabase's SQL editor, matches the PRD's "auditable event" framing | Plan |
| Raw record payload | Include full original row in `raw_record` | AGENTS.md's no-leakage rule explicitly carves out the Supabase log as the one place field values may live | Plan |
| Target DB write mode | Plain `INSERT`, not UPSERT | Watermark already prevents duplicates in normal operation; UPSERT risks silently masking real bugs | Plan |
| Write batching | Single batch call per write (executemany / one `.insert([...])`) | Naturally atomic, meets the 10k-records/60s NFR without manual transaction code | Plan |
| Supabase failure handling | Abort immediately, no retry | Matches every existing `_connect_*`/`_load_rules` failure convention in this codebase | Plan |
| Zero-valid-records | Watermark still advances | Every record already got a durable classification; re-processing the same all-invalid batch forever wastes throughput | Plan |
| Test coverage | Unit tests with mocks only | Matches existing `test_sync.py` convention; no live credentials needed in CI | Plan |
| DDL ownership | Checked-in `supabase/rejections.sql`, applied manually | Durable/reviewable schema record; tool never auto-mutates schema, consistent with Source/Target DB provisioning | Plan |
| Watermark value | Wall-clock `now()` at success, not max record timestamp | PRD FR-007/US-01 literally say "current run timestamp" | Plan |

## Scope

**In scope:**
- `_connect_target`, `_connect_supabase`, `_write_rejections_to_supabase`, `_commit_valid` in `dataguard/sync.py`
- `run_sync` restructure to pair rows with `RecordResult`s (zip)
- `write_watermark()` wiring into `run_sync`'s success path
- `supabase/rejections.sql` DDL
- `tests/test_sync.py` extensions (row-shaping, batching, abort, connection, orchestration)

**Out of scope:**
- `dataguard/cli.py`, `dataguard/watermark.py` internals (confirmed unnecessary by prior research)
- Retry/backoff, UPSERT, per-row writes, auto-applied DDL, integration test skeletons

## Architecture / Approach

`run_sync` zips `records` with `results` right after classification so both new write paths retain the original field values. `_write_rejections_to_supabase` batch-inserts into a fixed Supabase `rejections` table (one row per invalid/errored record, `table_name` column distinguishes source). `_commit_valid` batch-inserts into Target DB via a single dynamically-columned `executemany`. `write_watermark(now)` fires once, after both writes succeed, skipped in dry-run.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Supabase rejection logging | `run_sync` row-pairing restructure, `_write_rejections_to_supabase`, DDL | The restructure touches `run_sync`'s core split logic — must not disturb the write-order guarantee |
| 2. Target DB commit | `_connect_target`, `_commit_valid`, connection lifecycle | "The irreversible slice" per roadmap — a bug here corrupts Target DB |
| 3. Watermark write-on-success | `write_watermark()` wiring, first-ever full-`run_sync` orchestration tests | Getting the zero-valid-records-still-advances edge case wrong causes either infinite re-processing or silent record loss |

**Prerequisites:** S-02 (validation-engine) — done. Confirmed independent of S-04 (watermark-override) per `context/changes/roadmap/research.md`.
**Estimated effort:** ~2 sessions across 3 phases — this is the highest-complexity slice so far (two external systems, new schema, no prior precedent).

## Open Risks & Assumptions

- Supabase table name is hardcoded (`"rejections"`) — if a future multi-project or multi-environment setup needs configurability, that's a v2 change.
- No integration test exercises the real `supabase-py` API surface — an SDK breaking change wouldn't be caught until a live run (accepted tradeoff, matches your testing choice).
- `supabase/rejections.sql` must be applied manually before first use — nothing in this plan enforces or checks that at runtime.

## Success Criteria (Summary)

- A live sync run correctly splits records across Supabase (rejections, with full payload) and Target DB (valid only), advances the watermark exactly once per successful run, and aborts cleanly with no partial Target DB writes if the Supabase write fails.
- Full automated suite (`pytest -m "not integration"`, mypy, ruff) stays green throughout.
