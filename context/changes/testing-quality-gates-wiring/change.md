---
change_id: testing-quality-gates-wiring
title: Wire quality gates (pytest, mypy, ruff) into CI
status: complete
created: 2026-08-22
updated: 2026-08-29
archived_at: null
---

## Notes

Rollout Phase 1 of `context/foundation/test-plan.md`: "Quality-gates wiring".

Risks covered: cross-cutting.
Test types planned: gates.
Risk response intent: Wire pytest+mypy+ruff into CI on every push/PR so all existing and future tests actually run continuously — today's only GitHub Actions workflow (`.github/workflows/dataguard-sync.yml`) runs the production sync job and never executes pytest, mypy, or ruff.
