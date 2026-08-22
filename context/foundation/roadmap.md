---
project: DataGuard
version: 1
status: draft
created: 2026-06-05
updated: 2026-08-22
prd_version: 1
main_goal: speed
top_blocker: decisions
---

# Roadmap: DataGuard

> Derived from `context/foundation/prd.md` (v1) + auto-researched codebase baseline.
> Edit-in-place; archive when superseded.
> Slices below are listed in dependency order. The "At a glance" table is the index.

## Vision recap

Data engineers transfer records between two relational databases and have no reliable signal when the transfer fails silently — invalid or dropped records only surface hours later in downstream reports. DataGuard solves this by validating each record before it lands, writing only valid records to the target, and logging every rejection to Supabase with the exact rule violation attached, so a failed transfer produces an auditable event instead of a data gap.

## North star

**S-03: live sync with rejection logging and write-order guarantee** — the smallest end-to-end flow that proves DataGuard's core hypothesis: that a validation-first pipeline turns a silent data transfer failure into an auditable, recoverable event.

> "North star" in this roadmap means: the smallest end-to-end slice whose successful delivery proves the core product hypothesis — placed as early as its Prerequisites allow because everything else only matters if this works. "Validation-first" means: validate each record before any write occurs, write rejections to Supabase before committing valid records to Target DB, and abort the entire run if the audit trail cannot be written.

## At a glance

| ID   | Change ID              | Outcome (user can …)                                                                      | Prerequisites | PRD refs                               | Status   |
|------|------------------------|-------------------------------------------------------------------------------------------|---------------|----------------------------------------|----------|
| S-01 | source-db-extraction   | connect to Source DB and extract records since the watermark                              | —             | FR-001, FR-003, FR-004, FR-007         | done     |
| S-02 | validation-engine      | validate extracted records against a rules file and see a classification summary          | S-01          | FR-002, FR-003, FR-004, FR-005, FR-006 | done |
| S-03 | live-sync-write-cycle  | complete a live sync — rejections to Supabase, valid records to Target DB, watermark updated | S-02       | FR-003, FR-007, US-01                  | done |
| S-04 | watermark-override     | override the watermark with `--since` for testing or recovery                             | S-03          | FR-008                                 | proposed |

## Baseline

What's already in place in the codebase as of 2026-06-05 (auto-researched + user-confirmed). Slices below assume these are present and do NOT re-scaffold them.

- **Frontend:** absent — pure CLI (Typer + Rich); no web framework
- **Backend / API:** partial — `run_sync()` orchestrator with write-order logic and dry-run branch already coded in `dataguard/sync.py`; 6 implementation stubs awaiting body (`_connect_source`, `_connect_target`, `_extract`, `_classify`, `_write_rejections_to_supabase`, `_commit_valid`); `_load_rules()` implemented (reads and parses JSON); CLI command wired with all flags (`--table`, `--rules`, `--dry-run`, `--since`); `env_check.py` validates required env vars; `models.py` defines `Outcome` enum and `RecordResult`
- **Data:** partial — psycopg2-binary and Supabase SDK installed; `SOURCE_DB`, `TARGET_DB`, `SUPABASE_URL`, `SUPABASE_KEY` declared and validated; no DB connection or query code yet; `rules/orders.json` is an empty placeholder
- **Auth:** absent — by design (PRD §Access Control: single-user, possession-based access via `.env`)
- **Deploy / infra:** partial — `.github/workflows/dataguard-sync.yml` present (daily cron + manual dispatch); no Dockerfile
- **Observability:** absent — Rich `console.print()` only; no structured logging or error tracking

## Foundations

No foundations identified. The existing codebase scaffold (`run_sync()` orchestrator, CLI command, `env_check.py`, `models.py`) provides sufficient structure for implementation to proceed directly with vertical slices. All technical elements are introduced within the first slice that needs them.

## Slices

### S-01: Source DB extraction

- **Outcome:** user can connect to Source DB and extract records created since the watermark
- **Change ID:** source-db-extraction
- **PRD refs:** FR-001, FR-003 (extraction phase), FR-004, FR-007 (watermark read)
- **Prerequisites:** —
- **Parallel with:** —
- **Blockers:** —
- **Unknowns:**
  - What is the expected behavior when the Source DB table has no timestamp column — hard error, full-table fallback, or user-configurable? Owner: developer. Block: no (developer decides before implementing `_extract`).
  - What extraction strategy (streaming vs. batch fetch) satisfies the < 60s throughput NFR at 10,000 records? Owner: developer. Block: no.
- **Risk:** First slice; proves DB connectivity before any validation or write logic is built. psycopg2 version compatibility with the target PostgreSQL major version should be verified during implementation.
- **Status:** done

---

### S-02: Validation engine

- **Outcome:** user can validate extracted records against a rules file and see a color-coded classification summary (Total / Passed / Failed / Errored)
- **Change ID:** validation-engine
- **PRD refs:** FR-002, FR-003 (validation phase), FR-004, FR-005, FR-006
- **Prerequisites:** S-01
- **Parallel with:** —
- **Blockers:** —
- **Unknowns:**
  - What is the JSON schema for field-level rule definitions (null checks, type checks, comparison operators, regex)? `rules/orders.json` is currently an empty placeholder. Owner: developer. Block: no (developer defines schema before implementing `_classify`).
- **Risk:** Classification correctness is load-bearing — a valid record marked invalid silently corrupts Target DB and the rejection log is misleading. Test coverage on `_classify` is not optional. Note: FR-006 dry-run activates automatically once this slice is complete — the `run_sync()` orchestrator already contains the dry-run branch; no extra work required.
- **Status:** done

---

### S-03: Live sync write cycle

- **Outcome:** user can complete a live sync — rejections logged to Supabase with exact rule violations, valid records written to Target DB, write order enforced, watermark updated on success
- **Change ID:** live-sync-write-cycle
- **PRD refs:** FR-003 (write phase), FR-007 (watermark update), US-01
- **Prerequisites:** S-02
- **Parallel with:** —
- **Blockers:** —
- **Unknowns:**
  - What is the Supabase rejection log table schema (column names, types, required fields)? Owner: developer. Block: no (developer designs schema before implementing `_write_rejections_to_supabase`).
  - Should `_commit_valid` use INSERT or UPSERT semantics for Target DB writes? Owner: developer. Block: no.
- **Risk:** Write-order guarantee is the hardest constraint: Supabase write must succeed before any Target DB commit; if Supabase fails mid-run, the entire run must abort with no partial Target DB writes. This is the irreversible slice — a bug here corrupts Target DB or leaves the audit trail incomplete. Partial-write handling must be explicitly designed, not left to the happy path.
- **Status:** done

---

### S-04: Watermark override

- **Outcome:** user can pass `--since <timestamp>` to override the watermark and re-process records from a specific point for testing or recovery
- **Change ID:** watermark-override
- **PRD refs:** FR-008
- **Prerequisites:** S-03
- **Parallel with:** —
- **Blockers:** —
- **Unknowns:**
  - Should `--since` predating the existing watermark require an explicit acknowledgment flag, or a prominent warning only? Owner: developer. Block: no.
- **Risk:** A wrong `--since` timestamp causes duplicate records in Target DB. PRD §FR-008 requires a prominent warning before proceeding; this must be implemented before the slice ships.
- **Status:** proposed

## Backlog Handoff

| Roadmap ID | Change ID             | Suggested issue title                                                  | Ready for `/10x-plan` | Notes                               |
|------------|-----------------------|------------------------------------------------------------------------|-----------------------|-------------------------------------|
| S-01       | source-db-extraction  | Implement Source DB connection and incremental record extraction        | yes                   | Run `/10x-plan source-db-extraction` |
| S-02       | validation-engine     | Implement rules validation engine and classification summary            | no                    | Depends on S-01                     |
| S-03       | live-sync-write-cycle | Implement Supabase rejection logging and Target DB write cycle          | no                    | Depends on S-02; north star         |
| S-04       | watermark-override    | Implement `--since` watermark override with duplicate-protection warning | no                   | Depends on S-03                     |

## Open Roadmap Questions

1. **Source DB timestamp column behavior** — what does `dataguard sync` do when the source table has no timestamp column: hard error, full-table fallback, or user-configurable? Owner: developer. Block: S-01 implementation decision (resolve before implementing `_extract`).
2. **Rules JSON schema** — formal definition of the field-level rules format (null check, type check, comparison operators, regex). `rules/orders.json` is an empty placeholder. Owner: developer. Block: S-02 implementation decision.
3. **Supabase rejection log table schema** — column names, types, and required fields for the rejection log table. Owner: developer. Block: S-03 implementation decision.
4. **Acceptance criteria thresholds for US-01** — measurable thresholds currently deferred from PRD §Open Questions (e.g., "< 60s for 10k records" is implied by NFR but not formally stated). Owner: developer. Block: no; needed before S-03 can be verified.
5. **Errored records in summary** — *(resolved by existing implementation)* `cli.py:_print_summary` already shows Errored as a separate column alongside Total / Passed / Failed. PRD open question is closed.

## Parked

- **Cross-field validation rules** — Why parked: PRD §Non-Goals; deferred to v2. Resolution needed before v2 per PRD §Open Questions.
- **GUI / web dashboard** — Why parked: PRD §Non-Goals; terminal output is the only MVP interface.
- **Scheduling / orchestration** — Why parked: PRD §Non-Goals; engineer invokes manually or wires to an external scheduler.
- **Rollback / undo** — Why parked: PRD §Non-Goals; recovery from an incorrect sync is the engineer's responsibility.
- **Structured logging / observability beyond Rich** — Why parked: no NFR requires it for MVP; no throughput or audit use case demands it at ≤10k records/run.
- **Multi-table sync in one run** — Why parked: PRD scopes one `--table` per invocation; multi-table is not in any user story.

## Done

- **S-01: connect to Source DB and extract records since the watermark** — Archived 2026-07-11 → `context/archive/2026-06-05-source-db-extraction/`. Lesson: —.
- **S-02: validate extracted records against a rules file and see a color-coded classification summary (Total / Passed / Failed / Errored)** — Archived 2026-07-24 → `context/archive/2026-07-12-validation-engine/`. Lesson: —.
- **S-03: complete a live sync — rejections logged to Supabase with exact rule violations, valid records written to Target DB, write order enforced, watermark updated on success** — Archived 2026-08-22 → `context/archive/2026-07-24-live-sync-write-cycle/`. Lesson: —.
