---
change_id: auth-supabase-login
title: Auth supabase login
status: implementing
created: 2026-08-29
updated: 2026-08-29
archived_at: null
---

## Notes

Per-engineer Supabase Auth login gating every `dataguard` command, replacing the original
"possession of `.env` is the only access control" MVP model. Required amending
`context/foundation/prd.md` (§Access Control, new FR-009/FR-010, Open Questions #6-7) before
planning, since the original PRD explicitly ruled out any auth system.

Deliberately out of scope: account provisioning/self-signup, and attaching engineer identity
to Supabase rejection log rows (the PRD's stated "per-user attribution" rationale is not
realized by this change — see plan.md "What We're NOT Doing").
