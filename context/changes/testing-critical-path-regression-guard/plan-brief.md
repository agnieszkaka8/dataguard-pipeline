# Testing Critical Path Regression Guard — Plan Brief

> Full plan: `context/changes/testing-critical-path-regression-guard/plan.md`

## What & Why

Lock two AGENTS.md hard rules — write order (Supabase-before-Target-DB,
abort-on-Supabase-failure) and no-leakage (no credential or raw field
value in console output or error messages) — with dedicated, named
regression tests. This is `context/foundation/test-plan.md` §3 Phase 2,
covering Risk #2 and Risk #3 from the risk map.

## Starting Point

Both hard rules are already correctly implemented in `dataguard/sync.py`
and `dataguard/cli.py` — this is a coverage gap, not a bug. Write order
has an incidental, buried assertion inside an unrelated test
(`tests/test_sync.py:396-428`) that would fail on a real reordering but
isn't named or owned by the hard rule it protects. No-leakage has zero
coverage anywhere in the suite today.

## Desired End State

Two new test files each independently prove they're real regression
guards: temporarily reordering the write blocks, or temporarily adding a
`str(exc)`-style leak, makes the corresponding new test fail with a clear
message. The old incidental assertion is removed from `test_sync.py`.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Test file organization | New dedicated files (`test_write_order_regression.py`, `test_no_leakage_regression.py`) | File name itself signals which hard rule is at stake, fixing the discoverability gap that let Risk #2 go under-tested | Plan (user-confirmed) |
| Incidental write-order assertion in `test_sync.py` | Strip it out, new file becomes sole owner | One source of truth for the hard rule; avoids two mock setups drifting apart | Plan (user-confirmed) |
| No-leakage test depth | Generic sentinel sweep across all `console.print`/exception surfaces, not per-call-site hard-coding | Catches any future leak anywhere in `cli.py`/`sync.py`/`watermark.py`, not just today's known-safe lines | Plan (user-confirmed) |
| Priority if time is tight | Both risks must-have, no fallback | Both are AGENTS.md absolute hard rules, both High-impact in the risk map | Plan (user-confirmed) |

## Scope

**In scope:**
- New `tests/test_write_order_regression.py` (order proof + relocated abort-on-failure test)
- New `tests/test_no_leakage_regression.py` (sentinel sweep, all failure branches + success + dry-run)
- Removing the incidental order assertion from `tests/test_sync.py`

**Out of scope:**
- Any production code change (both hard rules are already correct)
- `typer.testing.CliRunner` (reserved for test-plan §3 Phase 4)
- Real Postgres/Supabase instances (test-plan §3 Phase 3/4)
- `env_check.py` (already correct, not a regression risk)
- New CI wiring (both files run under the existing required `pytest` gate)

## Architecture / Approach

Two independent, sequential phases — one file per risk. Phase 1 extends
an existing pattern (mock-call-order assertion against the real,
unmocked `run_sync`). Phase 2 introduces a new pattern: inject a sentinel
into fake DSNs and a record field, run every code path with mocked I/O
that echoes the sentinel back in its exceptions (matching real driver
behavior), and assert the sentinel never surfaces in captured
`console.print` calls or exception `str()`.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Write-order regression guard | Dedicated, named test proving order + abort behavior; old incidental assertion removed | Removing the old assertion could silently drop coverage if not fully replaced — mitigated by moving, not just adding |
| 2. No-leakage regression guard | First-ever no-leakage coverage via sentinel sweep across 3 modules | Missing a code path means the sweep isn't actually complete — mitigated by cross-checking against test-plan §2 row #3's call-site list |

**Prerequisites:** None — test-plan §3 Phase 1 (CI wiring) is already complete, so both new files run under CI automatically.
**Estimated effort:** ~1 session, 2 phases, test-only changes.

## Open Risks & Assumptions

- Assumes `psycopg2`/Supabase-client exceptions realistically echo back
  connection/value details in their messages (used to make the sentinel
  test meaningful) — this is standard driver behavior but not verified
  against a real instance in this phase (that's Phase 3/4's job).
- Manual verification steps (swap the code, confirm the test fails,
  revert) are required to trust these are real regression guards, not
  tautologies — see plan.md's Critical Implementation Details.

## Success Criteria (Summary)

- `uv run pytest tests/test_write_order_regression.py tests/test_no_leakage_regression.py -v` passes
- Full suite (`uv run pytest -m "not integration"`), `mypy dataguard/`, and `ruff` all stay clean
- Both new tests provably fail when the hard rule they guard is deliberately violated (then revert)
