# Quality-Gates Wiring Implementation Plan

## Overview

Add a new GitHub Actions workflow that runs `ruff check`, `mypy`, and `pytest -m "not integration"` on every push to `main` and every pull request, so the project's existing local quality gates actually execute continuously instead of only when a developer remembers to run them. This is rollout Phase 1 of `context/foundation/test-plan.md`.

## Current State Analysis

The repository has exactly one GitHub Actions workflow, `.github/workflows/dataguard-sync.yml`, and it is a production job: it runs the live `dataguard sync` command on a daily cron and via manual dispatch, installs the package from a pinned release tag (not the working tree), and requires four production secrets (`SOURCE_DB`, `TARGET_DB`, `SUPABASE_URL`, `SUPABASE_KEY`). It has no `push` or `pull_request` trigger. No other CI configuration exists anywhere in the repo. `ruff`, `mypy`, and `pytest` are already dev dependencies (`pyproject.toml` `[dependency-groups].dev`) with commands already documented in `AGENTS.md`, and all three currently pass cleanly against the committed code. The test suite never reaches `env_check()` (the only reader of the four secret-backed env vars), so no test job needs secrets.

## Desired End State

A new `.github/workflows/ci.yml` exists, triggers on push to `main` and on every pull request, and runs ruff, mypy, and pytest (excluding the one `integration`-marked test) in a single job. Pushing a commit that breaks any of the three gates produces a failing, visibly-named GitHub check on that commit/PR. Verification: push a deliberately broken commit (e.g. a ruff violation) to a branch with an open PR and confirm the check fails with that specific step reported; then revert and confirm it goes green.

### Key Discoveries:

- No `[tool.mypy]`, `[tool.ruff]`, or `[tool.pytest.ini_options]` section exists in `pyproject.toml` — both tools run on stock defaults today, so CI has no separate config to drift from.
- `tests/conftest.py` already registers the `integration` marker; `uv run pytest -m "not integration"` already correctly excludes the one real-DB test (`test_extract_real_db` in `tests/test_sync.py`) — no new marker plumbing needed.
- `uv sync` installs the `dev` dependency group by default (uv treats a group literally named `dev` as included unless `--no-dev` is passed), matching how `ruff`/`mypy`/`pytest` are already available locally via `uv run`.
- Branch-protection enforcement (marking the new check "required") is a GitHub Settings change outside repo files and could not be verified by `/10x-research` (`gh` CLI unavailable in that environment) — this plan treats "the workflow runs and reports a status check" and "the check blocks merging" as two separate deliverables, per the user's explicit choice.

## What We're NOT Doing

- Not extending `.github/workflows/dataguard-sync.yml` — its cron/dispatch triggers, pinned-tag install, and production secrets are incompatible with a push/PR-triggered CI gate (confirmed in research).
- Not adding `[tool.mypy]`/`[tool.ruff]`/`[tool.pytest.ini_options]` sections to `pyproject.toml` — out of scope; stock defaults are already clean and changing them is a separate decision.
- Not adding CI for Python versions other than 3.12 — `pyproject.toml` requires `>=3.12` with no matrix-relevant constraint.
- Not wiring Phase 2–5 tests (write-order/no-leakage regression, realistic Supabase payloads, Source DB/watermark integration, classification regression) — those are separate rollout phases with their own change folders.
- Not writing the branch-protection API/Terraform config programmatically — it's a manual GitHub Settings action, documented as a manual verification step below.

## Implementation Approach

Add one new workflow file, `.github/workflows/ci.yml`, modeled on the existing `dataguard-sync.yml`'s toolchain setup (`actions/checkout@v4`, `astral-sh/setup-uv@v5` with `python-version: "3.12"`) but with its own independent triggers, no secrets, and a single job with three sequential-but-all-run steps (ruff, mypy, pytest) so a single push surfaces every gate's failures at once rather than requiring a fix-one-push-again loop.

## Critical Implementation Details

**State sequencing (why "run all, then fail")**: GitHub Actions steps stop the job at the first non-zero exit by default. To satisfy the "run all, report all failures" decision, each of the three tool steps needs `continue-on-error: true` plus its outcome captured (e.g. into a step output or a `$GITHUB_ENV` flag), and a final step that checks whether any captured outcome was `failure` and exits non-zero itself — that final step is what makes the job's overall status red. Without that final aggregating step, `continue-on-error: true` alone would make the *job* report green even when a tool step failed, which would silently defeat the purpose of the gate.

## Phase 1: Add and verify the CI workflow

### Overview

Create `.github/workflows/ci.yml`, confirm it triggers and reports correctly on a real push/PR, then manually enable branch-protection enforcement.

### Changes Required:

#### 1. New CI workflow

**File**: `.github/workflows/ci.yml`

**Intent**: Run ruff, mypy, and pytest (excluding the `integration`-marked test) against the working tree on every push to `main` and every pull request, with no secrets required, surfacing all three tools' failures on a single run rather than stopping at the first one.

**Contract**:
- Triggers: `on.push.branches: [main]` and `on.pull_request` (no branch filter, per decision).
- Job `quality-gates`, `runs-on: ubuntu-latest`, no `env:` block (no secrets needed — confirmed by research: no test reaches `env_check()`).
- Steps, in order: `actions/checkout@v4` → `astral-sh/setup-uv@v5` with `python-version: "3.12"` (mirrors `dataguard-sync.yml`) → `uv sync` → a ruff step (`uv run ruff check .`) → a mypy step (`uv run mypy dataguard/`) → a pytest step (`uv run pytest -m "not integration"`) → an aggregating final step that fails the job if any prior step failed. Each of the three tool steps sets `continue-on-error: true` and records its `outcome` via `id:`; the aggregating step reads those three `steps.<id>.outcome` values (see Critical Implementation Details above for why this is needed).
- Set `NO_COLOR: "1"` on the job or workflow (matches the hard rule already applied in `dataguard-sync.yml`), so any rich console output in the pytest run stays machine-readable in CI logs.

### Success Criteria:

#### Automated Verification:

- [ ] Workflow YAML is syntactically valid: `actionlint .github/workflows/ci.yml` if available, otherwise a push that triggers the workflow without a GitHub Actions parse error counts as verification
- [ ] On a clean push to a branch with an open PR, the workflow run succeeds with ruff, mypy, and pytest all reported as passed
- [ ] `uv run pytest -m "not integration"` step reports "84 passed, 1 deselected" matching the local baseline from research (no drift in what CI excludes vs. runs)

#### Manual Verification:

- [ ] Push a commit with a deliberate ruff violation to a branch with an open PR; confirm the CI check fails and the failure is attributable to the ruff step specifically (not silently swallowed by `continue-on-error`)
- [ ] Revert that commit and confirm the check goes green again
- [ ] In GitHub → Settings → Branches, mark the new `quality-gates` check as a required status check for `main` (this is the enforcement step research flagged as unverifiable from repo contents alone — completing it here closes the gap between "the gate runs" and "the gate blocks merging")

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase (Phase 2 of the test-plan rollout, in its own change folder).

---

## Testing Strategy

### Unit Tests:

- N/A — this phase adds no application code; the "test" is the CI workflow itself.

### Integration Tests:

- N/A — no code paths change.

### Manual Testing Steps:

1. Open a throwaway PR with an intentional ruff violation (e.g. an unused import); confirm `ci.yml` fails and names the ruff step.
2. Fix the violation, push again; confirm the workflow goes green.
3. Confirm the workflow does NOT run on the `dataguard-sync` schedule/dispatch triggers (the two workflows stay independent).
4. Enable the check as required in branch protection; confirm a subsequent red PR is blocked from merging via the GitHub UI.

## Performance Considerations

None — a single lightweight job (`uv sync` + three CLI invocations) with no matrix, expected to complete in well under 5 minutes.

## Migration Notes

None — purely additive; no existing workflow, code, or config is modified.

## References

- Related research: `context/changes/testing-quality-gates-wiring/research.md`
- Pattern reference: `.github/workflows/dataguard-sync.yml` (toolchain setup steps to mirror)
- Test plan §5 Quality Gates: `context/foundation/test-plan.md` (this phase satisfies "required after §3 Phase 1" for both the lint+typecheck gate and the unit+integration gate)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Add and verify the CI workflow

#### Automated

- [x] 1.1 Workflow YAML is syntactically valid
- [ ] 1.2 Clean push reports ruff, mypy, and pytest all passed
- [ ] 1.3 Pytest step reports "84 passed, 1 deselected" matching local baseline

#### Manual

- [x] 1.4 Deliberate ruff violation causes an attributable CI failure
- [x] 1.5 Revert restores a green check
- [x] 1.6 New check marked required in GitHub branch protection for `main`
