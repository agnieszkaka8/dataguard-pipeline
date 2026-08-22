---
change_id: live-sync-write-cycle
title: Implement Supabase rejection logging and Target DB write cycle (S-03)
status: planned
created: 2026-07-24
updated: 2026-07-24
---

## Notes

S-03 from the roadmap — the north star slice. Implements `_connect_target`, `_write_rejections_to_supabase`, `_commit_valid` in `dataguard/sync.py`, plus wires `write_watermark()` into `run_sync` on success. See `context/foundation/roadmap.md` § S-03 for PRD refs (FR-003, FR-007, US-01) and risk notes. See `context/changes/roadmap/research.md` for the parallelizability assessment against S-04 (confirmed disjoint file footprint — this change touches `dataguard/sync.py` + `tests/test_sync.py` only, never `dataguard/watermark.py`'s internals or `dataguard/cli.py`).
