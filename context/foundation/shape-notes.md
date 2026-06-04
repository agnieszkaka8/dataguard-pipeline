---
project: "DataGuard"
context_type: greenfield
created: 2026-05-27
updated: 2026-05-30

checkpoint:
  current_phase: 8
  phases_completed: [1, 2, 3, 4, 5, 6, 7]
  gray_areas_resolved:
    - topic: "pain category"
      decision: "both visibility and data quality — engineer can't observe failures AND bad records land in the target"
    - topic: "primary persona scope"
      decision: "Data Engineers within a single organization; multi-user at org level, not multi-tenant"
    - topic: "access model"
      decision: "local config file (e.g. .env or dataguard.toml) holds DB credentials; access controlled by file possession"
    - topic: "role model"
      decision: "flat — all Data Engineers have identical access; no admin vs. member distinction"
  frs_drafted: 8
  quality_check_status: accepted
product_type: cli
target_scale:
  users: small
timeline_budget:
  mvp_weeks: 3
  after_hours_only: true
  hard_deadline: null
---

## Vision & Problem Statement

Data Engineers within a single organization transfer records between two relational databases. When a transfer runs, failures are silent: the process appears to succeed, but individual records are silently dropped, rejected, or corrupted. The engineer finds out too late — in downstream reports, dashboards, or consumer systems, hours or days after the run. Diagnosing the failure requires manual log excavation or ad-hoc DB queries, and by then invalid records may already be loaded into the target, corrupting dependent data.

The insight: the problem is compound — there is no observability (no signal when a record is lost or rejected) AND there is no gate (invalid records pass through unchallenged). A pipeline that validates each record against defined rules, loads only the valid set, and logs every rejection with a machine-readable reason turns a silent failure into an auditable event. The engineer gains both a stop signal and an audit trail without any manual investigation.

*Scale note: at 100x users, the local `.env` + `.watermark` model breaks — concurrent runs from multiple machines would conflict. That scale requires centralized state management and represents a fundamentally different product shape.*

## User & Persona

**Primary persona: Data Engineer (within a single org)**

A data engineer responsible for maintaining ETL/ELT pipelines between two relational databases at the same organization. They run transfers regularly — possibly on a schedule, possibly on demand. They are technically fluent: comfortable with a CLI, SQL, and reading structured logs. They do not need a GUI to do their job. Their pain is not that transfers are hard to run — it's that they have no reliable way to know whether a transfer completed cleanly or failed silently. They reach for DataGuard when they need to trust the data that lands in the target system.

## Non-Goals

- **No GUI or web dashboard**: terminal output is the only interface; no browser-based monitoring, reporting, or visualization for MVP.
- **No scheduling or orchestration**: the engineer invokes DataGuard manually or wires it to an external scheduler (cron, Airflow, etc.); the tool does not own or manage its own schedule.
- **No cross-field validation rules**: rules are field-level only for MVP (null checks, type checks, comparison operators, regex); rules that span two or more fields are deferred to a later version.
- **No rollback or undo**: DataGuard does not reverse a committed transfer; recovery from an incorrect sync is the engineer's responsibility.

## Access Control

Single user role; no authentication system. All Data Engineers at the organization share the same access level. Access is controlled by possession of a local config file (e.g., `.env` or `dataguard.toml`) that holds source and target database connection strings. The tool itself enforces no login, no session, no token — whoever can read the config file can run the full pipeline.

## Functional Requirements

### Configuration & Setup

- FR-001: Data Engineer can configure Source DB, Target DB, and Supabase connection strings in a local `.env` file. Priority: must-have
  > Socrates: Counter-argument considered: "shared .env risks credential exposure if accidentally committed." Resolution: kept for MVP in a single-org CLI context; documented as a known risk; engineers are responsible for .gitignore hygiene.

- FR-002: Data Engineer can define validation rules for a single table in a local JSON file and pass it to the CLI via `--rules <path>`. Priority: must-have
  > Socrates: Counter-argument considered: "JSON is too limited for complex cross-field rules." Resolution: kept, but rules format is explicitly scoped to simple field-level validations for MVP (null checks, type checks, comparison operators, regex). Cross-field rules are deferred to a later version — noted in Open Questions.

### Sync & Transfer

- FR-003: Data Engineer can run `dataguard sync --table <name> --rules <file>` to extract records from Source DB since the last watermark, validate each record against the rules file, write valid records to Target DB, and push every invalid record to the Supabase rejection log with the exact failure reason attached. Priority: must-have
  > Socrates: Counter-argument considered: "partial-write risk if one DB is unreachable mid-run — valid records land in Target DB but the Supabase log is incomplete." Resolution: kept; the write order and failure behavior (atomicity vs. best-effort) will be defined as a business logic rule in Phase 5.

- FR-004: Data Engineer can see real-time status messages in the console during a sync run (connecting → extracting → validating N records). Priority: must-have
  > Socrates: Counter-argument considered: "streaming progress output may slow large-table runs." Resolution: kept; real-time feedback is core to the tool's observability promise. Progress updates will be batched (e.g., every N records) rather than per-record to avoid stdout bottleneck — implementation constraint, not a change to the FR.

- FR-005: Data Engineer can see a color-coded summary table (Total / Passed / Failed) printed to the terminal at the end of each run. Priority: must-have
  > Socrates: Counter-argument considered: "color output breaks when piped to a file or CI log." Resolution: kept; color is the default for interactive terminal use. The tool will respect the NO_COLOR environment variable convention so piped/CI output stays clean.

- FR-006: Data Engineer can run `dataguard sync --dry-run` to validate records without writing to Target DB or the Supabase log. Priority: nice-to-have
  > Socrates: Counter-argument considered: "dry-run divergence creates false confidence if the live and dry-run code paths differ." Resolution: kept; the dry-run path must execute the same extraction and validation code as the live path — only the write step is skipped. Implementation must enforce this constraint.

### Incremental Tracking

- FR-007: Data Engineer can rely on automatic incremental sync — the tool saves a watermark (last successful run timestamp) to a local `.watermark` file and processes only records newer than the watermark on each subsequent run. Priority: must-have
  > Socrates: Counter-argument considered: "a lost or stale .watermark file causes silent data gaps or duplicates." Resolution: kept; the tool must treat a missing watermark as a first-run signal (process all records) and print an explicit warning when no watermark file is found. A corrupted or unreadable watermark is a hard error — not a silent fallback.

- FR-008: Data Engineer can override the automatic watermark by passing `--since <timestamp>` to manually set the extraction start point for testing or recovery. Priority: nice-to-have
  > Socrates: Counter-argument considered: "a wrong --since timestamp re-processes already-transferred records and causes duplicates in Target DB." Resolution: kept; the tool will print a prominent warning when --since predates the existing watermark, requiring explicit acknowledgment before proceeding. The duplication risk is owned by the engineer.

## Business Logic

DataGuard enforces a strict "Validation-First" contract, ensuring that the Target DB remains a trusted, clean data source by automatically quarantining any record that violates defined business rules before it can ever be committed.

**Inputs**: The rule consumes three user-facing inputs — the set of records extracted from Source DB within the engineer's defined time window (bounded by the watermark), the engineer's rules file defining field-level validation criteria for a single table, and the watermark itself (which gates which records enter the validation pipeline at all).

**Output**: Every record is classified into exactly one of three outcomes — valid (committed to Target DB), invalid (quarantined to Supabase rejection log with the exact rule violation attached), or errored (could not be evaluated — e.g., malformed or unparseable record — logged separately with an error reason). There is no ambiguous outcome; every record that enters the pipeline exits with a classification.

**Write order**: Rejection and error records are written to Supabase before any valid records are committed to Target DB. If the Supabase write fails, the entire run aborts — the Target DB is never ahead of the audit trail. This guarantees the log is always complete relative to what has landed in the target.

## Non-Functional Requirements

- **Throughput**: A sync run processing up to 10,000 records completes in user-perceivable time (target: under 60 seconds under normal network and DB conditions). Runs processing fewer records complete proportionally faster.
- **Exit behavior**: The tool exits with a non-zero status code on any failure — connection error, validation abort, or Supabase write failure. A shell script or CI pipeline can branch on the exit code without parsing console output.
- **Credential and payload privacy**: Connection strings, credentials, and raw record field values never appear in console output, local log files, or error messages. Rejected records and their field values are written only to the designated Supabase rejection log.
- **Retention**: The Supabase rejection log is append-only with no TTL enforced by the tool; cleanup is the engineer's responsibility.

## Open Questions

- **Cross-field validation rules**: FR-002 scopes rules to simple field-level validations for MVP. The question of how cross-field rules (e.g., "if status = shipped then tracking_id cannot be null") would be expressed in the rules format is deferred. Resolution needed before v2.
- **Errored records in the summary**: The business logic defines three outcomes (valid / invalid / errored). The summary table currently shows Total / Passed / Failed — it is unclear whether "errored" is a sub-category of Failed or a separate column. To be decided during implementation.
- **Source DB timestamp column requirement**: FR-007 (incremental sync) assumes the Source DB table has a reliable timestamp column for watermark-based extraction. The tool's behavior when no such column exists (hard error, full-table fallback, or user-configurable) is unresolved.

## Quality Cross-Check

All six quality elements present. Status: accepted on 2026-05-30.

| Element | Status |
|---|---|
| Access Control | present |
| Business Logic (one-sentence rule) | present |
| Project artifacts | present |
| Timeline-cost acknowledged | present |
| Non-Goals | present |
| Preserved behavior | n/a (greenfield) |

## User Stories

### US-01: Successful incremental sync run

**Given** the engineer has a `.env` with valid connections to Source DB, Target DB, and Supabase, and a `users_rules.json` defining validation rules for the `users` table,
**When** they run `dataguard sync --table users --rules users_rules.json`,
**Then** records created since the last watermark are extracted from Source DB; each record is validated against the rules; valid records are written to Target DB; invalid records are written to the Supabase rejection log with the exact failure reason; a color-coded summary (Total / Passed / Failed) is printed to the terminal; and the `.watermark` file is updated to the current run timestamp.

## Success Criteria

### Primary

A Data Engineer can run `dataguard sync --table <table> --rules <rules.json>` from the terminal. The CLI connects to Source DB, Target DB, and Supabase; streams real-time status to the console; validates each extracted record against the rules file; writes only valid records to Target DB; pushes every invalid record to the Supabase rejection log with the exact failure reason attached; and prints a color-coded summary (Total / Passed / Failed) at the end of the run — all within a single terminal session.

### Secondary

Dry-run mode: the engineer can invoke `dataguard sync --dry-run` to run extraction and validation without writing anything to Target DB. The console output and summary table behave identically to a real run, enabling the engineer to test and iterate on their rules file safely.

### Guardrails

- **Target DB stays clean**: no invalid record ever reaches the target — the validation gate is absolute.
- **Rejection log is complete**: every failed record is written to the Supabase log with an exact, machine-readable rejection reason; there are no silent drops.


