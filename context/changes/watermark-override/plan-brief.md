# Watermark Override Warning (S-04) — Plan Brief

> Full plan: `context/changes/watermark-override/plan.md`

## What & Why

`--since` already exists and already overrides the watermark — what's missing is FR-008's safety requirement: when the override predates the stored `.watermark`, the tool must print a prominent warning and require explicit acknowledgment before proceeding, since re-processing already-synced records risks duplicates in Target DB.

## Starting Point

`dataguard/cli.py` exposes `--since` and passes it straight to `run_sync()`; `dataguard/watermark.py`'s `read_watermark()` already honors it as an override. Neither compares the override against the currently stored watermark value — there's no risk check anywhere.

## Desired End State

`dataguard sync --since <early-timestamp>` prints a red warning and pauses for a yes/no confirmation before continuing; declining aborts with a non-zero exit before any DB connection. A `--since` at/after the stored watermark, or no `.watermark` file yet, behaves exactly as today — no prompt.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Acknowledgment mechanism | Interactive `typer.confirm(..., abort=True)` | Matches PRD's literal "explicit acknowledgment" wording; idiomatic Typer, auto non-zero exit on decline | Plan |
| Non-interactive bypass | None added | No automation path (cron workflow) ever passes `--since`; adding a `--force` flag now would be speculative | Plan |
| Where the check lives | New `check_since_override()` in `watermark.py`, called from a new `_guard_since_override()` in `cli.py` | Keeps user-facing prompting in the CLI layer (matches `env_check()`'s precedent) while the comparison logic stays pure and unit-testable | Plan |
| `read_watermark()` refactor | Extract file-read branch into private `_read_stored_watermark()` | Lets both `read_watermark` and `check_since_override` read the stored value without duplicating parse/error logic; zero change to `read_watermark`'s existing tested behavior | Plan |
| Test strategy for CLI logic | Unit-test `_guard_since_override` directly, not full CLI invocation | Matches this codebase's existing precedent (`test_sync.py` tests functions, not the Typer app end-to-end) | Plan |

## Scope

**In scope:**
- `check_since_override()` in `dataguard/watermark.py` (+ `_read_stored_watermark()` extraction)
- `_guard_since_override()` in `dataguard/cli.py`, wired into the `sync` command
- New `tests/test_cli.py`; extensions to `tests/test_watermark.py`

**Out of scope:**
- A `--force`/`--yes` bypass flag
- Any change to `read_watermark()`'s public contract or existing test behavior
- Persisting/logging the override decision

## Architecture / Approach

`_read_stored_watermark()` is the single source of truth for "what does `.watermark` currently say" — both `read_watermark` (existing) and `check_since_override` (new) call it. `cli.py`'s `sync` command calls `_guard_since_override(since)` right after `env_check()`; if the override is risky, it warns and gates on `typer.confirm`, which aborts (non-zero exit) on decline before any DB connection is opened.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Watermark comparison logic | `_read_stored_watermark()` extraction, `check_since_override()` | Must not change `read_watermark`'s existing tested behavior |
| 2. CLI warning and confirmation gate | `_guard_since_override()` wired into `sync` | Must abort before any DB connection is attempted on decline |

**Prerequisites:** S-03 (live-sync-write-cycle) — done.
**Estimated effort:** ~1 session across 2 phases — narrow, single-file-pair change.

## Open Risks & Assumptions

- Assumes an interactive TTY is available when `--since` is used with intent to override an existing watermark — true for the documented manual testing/recovery use case; the scheduled cron path never passes `--since`.

## Success Criteria (Summary)

- `--since` predating the stored watermark always warns and requires explicit confirmation before any write path runs.
- `--since` at/after the stored watermark, or with no watermark file yet, is a no-op change from today's behavior.
- Full automated suite (`pytest -m "not integration"`, mypy, ruff) stays green throughout.
