---
change_id: source-db-extraction
title: Implement Source DB connection and incremental record extraction (S-01)
status: archived
created: 2026-06-05
updated: 2026-07-11
archived_at: 2026-07-11T13:39:04Z
---

## Notes

S-01 from the roadmap. Implements `_connect_source` and `_extract` stubs in `dataguard/sync.py` so the CLI can connect to the Source DB and pull records created since the watermark. See `context/foundation/roadmap.md` § S-01 for PRD refs (FR-001, FR-003, FR-004, FR-007), open decisions, and risk notes.
