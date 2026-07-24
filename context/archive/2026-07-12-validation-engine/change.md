---
change_id: validation-engine
title: Implement rules validation engine and classification summary (S-02)
status: archived
created: 2026-07-12
archived_at: 2026-07-24T19:13:21Z
updated: 2026-07-24
---

## Notes

S-02 from the roadmap. Implements the `_classify` stub in `dataguard/sync.py` so each extracted record is validated against a field-level rules file and classified as valid / invalid / errored. See `context/foundation/roadmap.md` § S-02 for PRD refs (FR-002, FR-003, FR-004, FR-005, FR-006), open decisions, and risk notes. Roadmap Open Question #2 ("Rules JSON schema") is the primary unknown this change resolves.
