# Test Plan

> Phased test rollout for this project. Strategy is frozen at the top
> (§1–§5); cookbook patterns at the bottom (§6) fill in as phases ship.
> Read before writing any new test.
>
> Refresh: re-run `/10x-test-plan --refresh` when stale (see §8).
>
> Last updated: 2026-08-22

## 1. Strategy

Tests follow three non-negotiable principles for this project:

1. **Cost × signal.** The cheapest test that gives a real signal for the
   risk wins. Do not promote to e2e because e2e "feels safer." Do not put a
   vision model on top of a deterministic visual diff that already catches
   the regression.
2. **User concerns are first-class evidence.** Risks anchored in "the
   team is worried about X, and the failure would surface somewhere in
   area Y" carry the same weight as PRD lines or hot-spot data.
3. **Risks are scenarios, not code locations.** This plan documents *what
   could fail* and *why we believe it's likely* — drawn from documents,
   interview, and codebase *signal* (churn, structure, test base). It does
   NOT claim to know which line owns the failure. That knowledge is
   produced by `/10x-research` during each rollout phase. If the plan and
   research disagree about where the failure lives, research is the
   ground truth.

Hot-spot scope used for likelihood weighting: `dataguard/` (12 commits/30d;
`tests/` mirrors the same churn 1:1 and was not scored separately).

## 2. Risk Map

The top failure scenarios this project must protect against, ordered by
risk = impact × likelihood. Risks are failure scenarios in user / business
terms, not test names. The Source column cites the *evidence that surfaced
this risk* — never a specific file as "where the failure lives" (that is
research's job, see §1 principle #3).

| # | Risk (failure scenario) | Impact | Likelihood | Source (evidence — not anchor) |
|---|---|---|---|---|
| 1 | A rejected/errored record's audit-trail write to Supabase silently fails or the whole run aborts, for record shapes the write path was never exercised against | High | High | interview Q1, interview Q2, PRD Guardrails ("rejection log is complete... no silent drops"), archive/live-sync-write-cycle/plan.md impl-review |
| 2 | The write-order guarantee (Supabase-before-Target-DB, abort-on-Supabase-failure) regresses in a future change to `run_sync`'s orchestration | High | High | interview Q3, hot-spot dir `dataguard/` (12 commits/30d, concentrated in the orchestrator), roadmap §S-03 ("irreversible slice... bug here corrupts Target DB"), AGENTS.md write-order hard rule |
| 3 | A credential or a raw record field value leaks into console output, an error message, or a local log | High | Medium | AGENTS.md no-leakage hard rule, PRD NFR "Credential and payload privacy," abuse/security lens (secret/PII leakage — mandatory: tool handles DB credentials and forwards user-supplied record data) |
| 4 | A `--since` override or watermark misuse re-processes and double-commits already-synced records into Target DB | Medium | Medium | PRD FR-008 Socrates resolution, roadmap §S-04 risk note, interview Q4 (second-choice option) |
| 5 | A record that should fail validation is classified VALID (or vice versa), breaking the "Target DB stays clean" guardrail | High | Low | PRD Guardrails ("Target DB stays clean... validation gate is absolute"), roadmap §S-02 risk note |
| 6 | A timezone or precision mismatch at the watermark boundary silently drops or duplicates the record sitting exactly at the edge | Medium | Low | PRD FR-007 Socrates resolution ("a lost or stale .watermark file causes silent data gaps or duplicates") |

**Challenger findings:** SQL/command injection via user-supplied table or
column identifiers was considered under the mandatory abuse lens and
excluded from the top risks — `psycopg2.sql.Identifier` closes this class
structurally (per `archive/source-db-extraction/plan.md`'s Critical
Implementation Details), so a dedicated risk row would test the library,
not this project's logic. Risk #5 was kept despite its High/Low score —
unlike an unactionable external-outage scenario, it is cheaply testable at
the unit layer the codebase already invests in; its response guidance
below is framed as regression-lock, not new coverage.

### Risk Response Guidance

| Risk | What would prove protection | Must challenge | Context `/10x-research` must ground | Likely cheapest layer | Anti-pattern to avoid |
|------|-----------------------------|----------------|--------------------------------------|-----------------------|-----------------------|
| #1 | A rejected/errored record with realistic Source DB value types (timestamp, numeric, uuid, null) lands in the Supabase log with its payload intact — run doesn't abort, record isn't dropped | "The mocked `.insert().execute()` call succeeding proves the real write succeeds" — a mock proves the call shape, not that the payload actually serializes | Which Python types `psycopg2` returns for common Source DB column types; what the Supabase client's real serialization boundary does with each; whether the current fix covers all of them | contract / integration | Fixture data chosen because it's easy to write (int/str only), not because it matches what extraction actually returns — this is exactly how the original bug slipped past 20 passing tests |
| #2 | When the Supabase write fails, no Target DB connection is ever opened and no valid record is ever committed — proven against the actual call sequence | "Existing tests already assert call order, so the guarantee is covered" — that proves today's code order, not that a future edit preserves it; nothing currently fails CI if the two blocks are swapped | Whether a dedicated, named regression test exists that fails specifically ("write-order violated") vs. an incidental assertion buried in a broader test | unit / contract | Treating "the orchestration test still passes" as proof — an implementation-mirror test that asserts today's sequence without actively trying to violate it |
| #3 | Console output and every raised exception's `str()`, across every code path (success, every failure branch, dry-run), contains no connection string or raw field value | "We already print `type(exc).__name__` instead of `str(exc)`, so we're safe" — one contributor adding a "helpful" `str(exc)` message is one line from leaking a DSN | Every `console.print` call site across `cli.py`/`sync.py`/`watermark.py`; what each exception type's default string form contains for this stack | unit (captured-output) | Manual eyeballing during code review instead of an automated assertion — catches it only when a reviewer remembers to check |
| #4 | A real interactive `--since <early-timestamp>` run pauses, shows the warning, and genuinely blocks on decline / proceeds on confirm | "The `typer.confirm` call is mocked and asserted, so the safety gate is proven" — proves the code path is reached, not that a real terminal session blocks correctly or that non-interactive input is handled sanely | What happens with `--since` in a genuinely non-interactive context (piped stdin); how the exit code surfaces to a calling shell | integration (CLI-runner) | Deferring this to "manual verification" indefinitely, as both prior slices did — a CLI-runner test costs little more than the existing mock |
| #5 | For every rule type in the locked schema, a record that should fail still fails, and a record that passes today keeps passing after future edits to the evaluator or the shipped rules file | "The existing suite already covers every check type, so this risk is closed" — thorough unit coverage of the evaluator in isolation doesn't prove the shipped rules file, or a future edit, doesn't quietly widen VALID | Whether any test exercises the real shipped rules file against a representative row set, not only synthetic unit fixtures | unit (table-driven) | Promoting to integration/e2e "to be safe" when the unit layer already gives full, cheap signal here |
| #6 | A record exactly at the stored watermark, and one just before/after it, are each extracted exactly once across two consecutive runs | "`timestamp_col > since_dt` in the SQL is obviously correct" — timezone-naive vs. aware mismatches or column precision can silently shift the boundary by a whole record | What timestamp precision and timezone-awareness the real Source DB columns use; whether `psycopg2` returns naive or aware datetimes for them | integration (real Postgres) | A mocked-cursor test asserting "WHERE" appears in the query string (today's actual assertion) — proves the SQL was built, not that the boundary is correct |

## 3. Phased Rollout

Each row is a discrete rollout phase that will open its own change folder
via `/10x-new`. Status moves left-to-right through the values below; the
orchestrator updates Status as artifacts appear on disk.

| # | Phase name | Goal (one line) | Risks covered | Test types | Status | Change folder |
|---|---|---|---|---|---|---|
| 1 | Quality-gates wiring | Wire pytest+mypy+ruff into CI on every push/PR — today's only workflow runs the production sync job, never a test | cross-cutting | gates | complete | `context/changes/testing-quality-gates-wiring/` |
| 2 | Critical-path regression guard | Lock the write-order guarantee and the no-leakage hard rule with dedicated tests that fail loudly if violated | #2, #3 | unit + contract | change opened | `context/changes/testing-critical-path-regression-guard/` |
| 3 | Realistic Supabase payload coverage | Prove the rejection-audit-trail write path survives real Source DB data shapes, not hand-picked fixtures | #1 | contract + integration | not started | — |
| 4 | Source DB & watermark integration coverage | Prove the extraction boundary and the `--since` confirm gate behave correctly against real conditions, not mocks | #4, #6 | integration + CLI-runner | not started | — |
| 5 | Classification regression protection | Extend the already-strong rules-engine suite so future evaluator/rules-file edits can't silently widen VALID | #5 | unit (table-driven) | not started | — |

**Status vocabulary** (fixed — parser literals): `not started` → `change opened` → `researched` → `planned` → `implementing` → `complete`.

## 4. Stack

The classic test base for this project. Recommendations are grounded in
local manifests/configs plus the MCP/tools actually exposed in the current
session.

| Layer | Tool | Version | Notes |
|---|---|---|---|
| unit + integration | pytest | >=9.0.3 | existing dev dependency; `-m "not integration"` split already wired via `conftest.py` |
| type checking | mypy | >=2.1.0 | enforced per AGENTS.md convention |
| lint + format | ruff | >=0.15.15 | linter and formatter; no separate config file |
| mocking | `unittest.mock` (stdlib) | n/a | existing convention across all four test files; no dedicated mocking library added |
| CLI interaction testing | none yet — see Phase 4 | n/a | `typer.testing.CliRunner` ships with `typer` but is not yet used anywhere in the suite |
| local Postgres/Supabase harness | none yet — see Phase 3, Phase 4 | n/a | all DB/Supabase calls are currently mocked; no docker-compose or local test-instance setup exists |
| e2e / accessibility | not applicable | n/a | DataGuard is a headless CLI with no browser/DOM surface |

**Stack grounding tools (current session):**
- Docs: Context7 — available, not yet queried; natural first stop for supabase-py/postgrest and `typer.testing.CliRunner` API details when Phase 3/4 are planned; checked: 2026-08-22.
- Search: Exa.ai — available, not used in this discovery pass; available to verify current supabase-py/postgrest testing guidance during Phase 3 planning; checked: 2026-08-22.
- Runtime/browser: none available, and none needed — no browser/DOM surface exists; checked: 2026-08-22.
- Provider/platform: none wired — the repo's only CI integration is GitHub Actions YAML (`.github/workflows/dataguard-sync.yml`), configured directly, not via an MCP; checked: 2026-08-22.

## 5. Quality Gates

The full set of gates that must pass before a change reaches production.
"Required for §3 Phase N" means the gate is enforced once that rollout
phase lands; before that, the gate is `planned`.

| Gate | Where | Required? | Catches |
|---|---|---|---|
| lint + typecheck | local + CI | required after §3 Phase 1 | syntactic/type drift (today: local-only) |
| unit + integration (`-m "not integration"`) | local + CI | required after §3 Phase 1 | logic regressions (today: local-only — the only workflow runs the production sync job, never `pytest`) |
| write-order & no-leakage regression tests | CI on PR | required after §3 Phase 2 | the two AGENTS.md absolute hard rules regressing silently |
| realistic-payload contract test (Supabase write path) | local + CI | required after §3 Phase 3 | the exact bug class S-03's impl-review caught (non-JSON-native field types) |
| integration tests (Source DB boundary, CLI confirm gate) | local (needs test DB) | required after §3 Phase 4 | watermark-boundary and interactive-gate regressions mocks can't see |
| post-edit hook | local (agent loop) | optional — candidate for Module 3 Lesson 3 | fast regression signal on `dataguard/sync.py` and `dataguard/rules.py`, the two churn hot-spots |
| e2e on critical flows | n/a | not applicable | no UI/browser surface exists |

## 6. Cookbook Patterns

How to add new tests in this project. Each sub-section fills in once the
relevant rollout phase ships; before that it reads "TBD — see §3 Phase N."

### 6.1 Adding a unit test

- TBD — see §3 Phase 2.

### 6.2 Adding an integration test (real Source/Target DB or Supabase)

- TBD — see §3 Phase 3 (Supabase payload) and Phase 4 (Source DB boundary).

### 6.3 Adding a CLI-interaction test (`typer.testing.CliRunner`)

- TBD — see §3 Phase 4.

### 6.4 Adding a write-order / no-leakage regression test

- TBD — see §3 Phase 2.

### 6.5 Wiring a new CI gate

- TBD — see §3 Phase 1.

### 6.6 Per-rollout-phase notes

(Fills in after each phase lands, capturing anything surprising the phase taught.)

## 7. What We Deliberately Don't Test

Exclusions agreed during the rollout (Phase 2 interview, Q5). Future
contributors should respect these unless the underlying assumption changes.

- **CLI help text / `--help` output formatting** — Typer-generated; asserting exact wording is busywork that breaks on every Typer version bump. Re-evaluate if help text becomes a documented, user-facing contract. (Source: Phase 2 interview Q5.)

## 8. Freshness Ledger

- Strategy (§1–§5) last reviewed: 2026-08-22
- Stack versions last verified: 2026-08-22
- AI-native tool references last verified: 2026-08-22

Refresh (`/10x-test-plan --refresh`) when:

- a new top-3 risk surfaces from the roadmap or archive,
- a recommended tool's `checked:` date is older than three months,
- the project's tech stack changes (new framework, new test runner),
- §7 negative-space no longer matches what the team believes.
