---
date: 2026-07-24T21:24:43+02:00
researcher: Claude (10x-research)
git_commit: 65174507e7d4cedba00dcda98157be0a55193da1
branch: main
repository: dataguard-piepline
topic: "S-03 vs S-04 parallelizability via git worktrees — TPM assessment"
tags: [research, roadmap, parallel-work, worktrees, s-03, s-04, sync, watermark]
status: complete
last_updated: 2026-07-24
last_updated_by: Claude (10x-research)
---

# Research: S-03 vs S-04 parallelizability via git worktrees

**Date**: 2026-07-24T21:24:43+02:00
**Researcher**: Claude (10x-research)
**Git Commit**: 65174507e7d4cedba00dcda98157be0a55193da1
**Branch**: main
**Repository**: dataguard-piepline

## Research Question

Per `context/foundation/roadmap.md`, evaluate the next two slices — S-03 (`live-sync-write-cycle`) and S-04 (`watermark-override`) — for whether they can be planned and implemented in parallel using git worktrees. Pay strict attention to shared files, database contracts, pipeline layers, and dependencies. Is S-04 completely independent of S-03, or does it strictly depend on S-03 being merged first? Provide a clear TPM recommendation.

## Summary

**S-03 and S-04 have disjoint source-file footprints and no hard code-level dependency, despite the roadmap listing S-04's Prerequisites as S-03.** S-03 exclusively touches `dataguard/sync.py` + `tests/test_sync.py` (filling `_connect_target`, `_write_rejections_to_supabase`, `_commit_valid`, plus adding a `write_watermark(...)` call). S-04 exclusively touches `dataguard/watermark.py` + `tests/test_watermark.py` (restructuring `read_watermark`'s control flow to compare `--since` against the stored watermark), with `dataguard/cli.py` as a *conditional* touch only if S-04's own open design question resolves toward requiring a new `--acknowledge`/`--force` flag. Neither slice's required source changes touch the other's file set.

The roadmap's "Prerequisites: S-03" for S-04 reads as a **narrative/value-sequencing choice** (a watermark-override recovery flag is more meaningful once a live sync that actually writes exists), not a **technical blocker** — S-04's read-side logic depends only on the `.watermark` file mechanism, which is already fully implemented and unit-testable in isolation (`write_watermark`/`read_watermark` in `dataguard/watermark.py` both already exist and pass tests today, independent of whether `run_sync` calls `write_watermark` yet).

**TPM recommendation: parallelize.** Run `/10x-plan` for both S-03 and S-04 now, and implement both in separate git worktrees concurrently. The only real coupling is a *manual end-to-end verification* dependency, not a coding one — see "Open Questions" and "Architecture Insights" below for the exact conditions to manage that.

## Detailed Findings

### S-03 (`live-sync-write-cycle`) — file surface

- Three stub functions to fill in `dataguard/sync.py`: `_connect_target()` ([dataguard/sync.py:86-87](../../../dataguard/sync.py#L86-L87)), `_write_rejections_to_supabase()` ([dataguard/sync.py:134-135](../../../dataguard/sync.py#L134-L135)), `_commit_valid()` ([dataguard/sync.py:138-139](../../../dataguard/sync.py#L138-L139)) — all currently `raise NotImplementedError`.
- `run_sync`'s write-order orchestration is already correctly wired: rejections (`_write_rejections_to_supabase`) are called before Target DB connect/commit ([dataguard/sync.py:51-64](../../../dataguard/sync.py#L51-L64)) — S-03 fills bodies, doesn't restructure the order.
- **`write_watermark()` is defined in `dataguard/watermark.py:36` but has zero call sites anywhere in the repo outside its own test.** The roadmap's S-03 outcome text ("...watermark updated on success") is not yet implemented — S-03 must add a `write_watermark(...)` call inside `run_sync`, most naturally after `_commit_valid` succeeds and before `return results`, guarded to skip in `dry_run` mode. This call requires only importing `write_watermark` alongside the existing `read_watermark` import in `dataguard/sync.py:15` — it does **not** require editing `dataguard/watermark.py` itself.
- No Supabase client wrapper exists yet; `supabase` (declared in `pyproject.toml`) is not imported anywhere in `dataguard/*.py`. Whether S-03 adds a new `dataguard/supabase_client.py` or inlines the client in `sync.py` is an open implementation choice.
- **Supabase rejection log table schema is undecided** — flagged as an open question in the roadmap itself ([context/foundation/roadmap.md:94, 127](../../../context/foundation/roadmap.md#L94)) and confirmed nowhere else in the repo defines it.
- Files touched: `dataguard/sync.py`, `tests/test_sync.py`, possibly a new `dataguard/supabase_client.py`. Does not need `dataguard/watermark.py`, `dataguard/cli.py`, `dataguard/models.py`.

### S-04 (`watermark-override`) — file surface

- `--since` is already fully wired end-to-end from S-01's scaffold: `cli.py:27-29` (typer option) → `cli.py:48` (passed to `run_sync`) → `sync.py:39` (`read_watermark(since)`). This is baseline, not S-04 work.
- Current `read_watermark(since)` ([dataguard/watermark.py:12-33](../../../dataguard/watermark.py#L12-L33)) short-circuits immediately when `since is not None` ([watermark.py:18-19](../../../dataguard/watermark.py#L18-L19)) — it never reads `.watermark` in that branch, so there is currently **no comparison** against the existing stored watermark.
- PRD FR-008 ([context/foundation/prd.md:86-87](../../../context/foundation/prd.md#L86-L87)) requires "a prominent warning when `--since` predates the existing watermark, requiring explicit acknowledgment before proceeding" — but the roadmap explicitly lists this as an **open, unresolved Unknown** for S-04 ([roadmap.md:110](../../../context/foundation/roadmap.md#L110)): warning-only vs. an explicit acknowledgment flag. `Block: no` — developer decides during implementation.
- Implementing the predates-check requires restructuring `read_watermark`'s control flow (not additive logic) so the `since`-provided branch also reads and parses `.watermark` for comparison, handling missing/corrupted-file cases in that branch too.
- If the Unknown resolves toward "explicit acknowledgment," a new CLI flag (e.g. `--force`) would be added in `dataguard/cli.py` near the existing `since` option. If it resolves to "warning only," `dataguard/cli.py` is untouched.
- **`dataguard/sync.py` is not needed at all** — `run_sync` already calls `read_watermark(since)` and does nothing else with the raw string; all new S-04 logic is internal to `watermark.py`'s return behavior, which keeps the same `datetime | None` return type.
- Files touched: `dataguard/watermark.py` (definite), `tests/test_watermark.py` (definite), `dataguard/cli.py` (conditional on the open Unknown).

### Cross-slice file overlap matrix

| File | S-03 | S-04 |
|---|---|---|
| `dataguard/sync.py` | edits (fills 3 stubs + adds `write_watermark` call/import) | not touched |
| `tests/test_sync.py` | edits (new tests) | not touched |
| `dataguard/watermark.py` | not touched (only calls an already-existing exported function from sync.py) | edits (restructures `read_watermark`) |
| `tests/test_watermark.py` | not touched | edits (new tests) |
| `dataguard/cli.py` | not touched | conditional edit (only if acknowledgment-flag path chosen) |

**Zero overlapping files across both slices' required source changes**, in every branch of S-04's open design question.

### Historical precedent

- No prior parallel-work precedent in this repo: `git log` shows a strictly linear history (S-01 → S-02, single `main` branch, one worktree). `context/foundation/lessons.md` contains only one entry (Rich `Console(no_color=...)` convention) — nothing about branching/worktree strategy.
- S-01's impl-review caught a real write-order bug in `dataguard/sync.py` — `_connect_target()` was originally called before `_write_rejections_to_supabase()` — and explicitly flagged: "S-03 would have triggered the bug when implementing `_connect_target()`" ([context/archive/2026-06-05-source-db-extraction/reviews/impl-review.md](../../archive/2026-06-05-source-db-extraction/reviews/impl-review.md)). This bug is already fixed in the current `run_sync` (verified directly: rejections logged before target connect/commit, [sync.py:51-64](../../../dataguard/sync.py#L51-L64)) — S-03 inherits a corrected scaffold. This is a signal that `dataguard/sync.py`'s write-order is delicate and load-bearing across every slice that touches it, reinforcing that S-03 (the sole owner of `sync.py` in this pairing) should have strong test coverage regardless of parallelization.
- No Supabase schema design and no watermark-override design exist anywhere in prior `context/changes/**/` or `context/archive/**/` artifacts beyond what's already in the roadmap/PRD. No `context/changes/watermark-override/` folder exists yet — S-04 research/planning hasn't started.

## Code References

- `dataguard/sync.py:15,39,51-64,86-87,134-135,138-139` — `read_watermark` import/call, write-order orchestration, the three S-03 stubs
- `dataguard/watermark.py:12-38` — `read_watermark` (S-04's edit target), `write_watermark` (S-03's new call site, unmodified)
- `dataguard/cli.py:27-29,48` — existing `--since` wiring (baseline, not S-04 work)
- `context/foundation/roadmap.md:85-97` — S-03 section (outcome, prerequisites, unknowns, risk)
- `context/foundation/roadmap.md:101-112` — S-04 section (outcome, prerequisites, unknowns, risk)
- `context/foundation/prd.md:86-87` — FR-008, the "prominent warning... explicit acknowledgment" requirement
- `context/foundation/prd.md:104` — Business Logic write-order hard rule S-03 must implement
- `context/archive/2026-06-05-source-db-extraction/reviews/impl-review.md` — prior write-order bug, explicitly named as an S-03 risk

## Architecture Insights

- The codebase's stub-based scaffolding (`run_sync` orchestrator + 6 named stubs from S-01) was deliberately designed so each roadmap slice owns a disjoint subset of stubs — S-01 owned `_connect_source`/`_extract`, S-02 owned `_classify`, S-03 owns `_connect_target`/`_write_rejections_to_supabase`/`_commit_valid`. This same design principle extends cleanly to S-04, which owns no stub at all — it only refines an already-complete function (`read_watermark`) in a separate module. The scaffold's file-per-concern boundary (`sync.py` = pipeline orchestration, `watermark.py` = watermark I/O, `cli.py` = argument parsing) is exactly what makes S-03/S-04 safe to parallelize: the boundary was already drawn before either slice started.
- The one genuine coupling between the two slices is **not code, it's verification**: S-04's stated purpose (PRD FR-008's risk note: prevent duplicate records in Target DB from a bad `--since`) can only be manually end-to-end verified once S-03's `_commit_valid` actually writes to Target DB. Until then, S-04's warning/acknowledgment behavior is fully verifiable via unit tests against `.watermark` file state and via `--dry-run` mode (which already exists and prints console output without writing anywhere) — but the "did this actually prevent a duplicate row" claim has no real Target DB to check against until S-03 ships.

## Historical Context (from prior changes)

- `context/archive/2026-06-05-source-db-extraction/plan.md` and `context/archive/2026-07-12-validation-engine/plan.md` both modified `dataguard/sync.py` and `tests/test_sync.py` — confirming `sync.py` is the repo's most contested file across slices historically, though never contested *concurrently* since all prior work was sequential.
- `context/archive/2026-06-05-source-db-extraction/reviews/impl-review.md` (finding F4) is the most relevant historical signal: a write-order bug in `sync.py` was caught specifically because the reviewer reasoned forward to S-03's future implementation. This validates that `sync.py`'s correctness is cross-slice-sensitive — good reason to keep S-03's own test coverage rigorous, independent of the parallelization question.

## Related Research

- `context/archive/2026-07-12-validation-engine/research.md` — internal+external research for S-02, same repo/lineage, no direct overlap with S-03/S-04 scope.

## Open Questions

1. **Is "Prerequisites: S-03" in the roadmap meant as a hard technical gate, or a narrative/value-sequencing note?** This research found no code-level dependency, but the roadmap's own field doesn't distinguish the two meanings. Recommend confirming intent with whoever authored the roadmap before committing to parallel worktrees, since overriding a documented prerequisite is a process/communication decision as much as a technical one.
2. **S-04's open design question (warning-only vs. explicit acknowledgment flag) should be resolved at `/10x-plan` time for `watermark-override`**, independent of S-03 — nothing about S-03 informs this decision, so there's no reason to defer it.
3. **How should S-04 verify its "prevents duplicate Target DB records" claim without waiting for S-03?** Two options: (a) mock a Target DB connection in S-04's own test suite to assert the warning fires before any hypothetical write, deferring only the true end-to-end manual check until after S-03 merges; (b) accept that S-04's manual verification step stays open/pending until S-03 lands, even if S-04's code and automated tests are complete earlier. This is a planning-time decision, not a blocker to starting S-04's implementation.

## TPM Recommendation

**Parallelize S-03 and S-04 in separate git worktrees.** The file-level evidence is unambiguous: zero overlap in required source changes across every branch of S-04's open design question. The roadmap's stated "Prerequisites: S-03" reflects a *value-narrative* ordering (a recovery flag matters more once live writes exist), not a *code* dependency — S-04's mechanism (`.watermark` file read/write) is already fully implemented and independently testable today.

Recommended sequence:
1. Run `/10x-plan live-sync-write-cycle` and `/10x-plan watermark-override` now, in either order — planning has no shared-resource cost.
2. Set up two worktrees (`git worktree add ../dataguard-s03 -b live-sync-write-cycle` and `../dataguard-s04 -b watermark-override`) and implement concurrently.
3. In S-04's plan, explicitly scope its automated test suite to cover the predates-watermark warning via mocked/seeded `.watermark` state (no Target DB dependency needed) — defer only its true end-to-end "no duplicate rows" manual check until after S-03 merges.
4. Merge whichever slice finishes first; the other rebases cleanly since there's no file contention.
5. Since this is the team's first parallel-worktree exercise on this repo (no prior precedent), consider recording a `context/foundation/lessons.md` entry after the fact capturing what did/didn't go smoothly — useful given Module 2 Lesson 5's guidance that parallelism should be capped at review capacity, not file-safety alone.
