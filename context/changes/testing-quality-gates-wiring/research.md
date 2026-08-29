---
date: 2026-08-22T20:19:39Z
researcher: Claude
git_commit: 03c252680392ffd054c0fd057a8d4c04ab047057
branch: main
repository: agnieszkaka8/dataguard-pipeline
topic: "Rollout Phase 1 (Quality-gates wiring): wire pytest+mypy+ruff into CI"
tags: [research, codebase, ci, github-actions, quality-gates, test-plan]
status: complete
last_updated: 2026-08-22
last_updated_by: Claude
---

# Research: Quality-gates wiring — grounding for test-plan.md rollout Phase 1

**Date**: 2026-08-22T20:19:39Z
**Researcher**: Claude
**Git Commit**: 03c252680392ffd054c0fd057a8d4c04ab047057
**Branch**: main
**Repository**: agnieszkaka8/dataguard-pipeline

## Research Question

Ground rollout Phase 1 of `context/foundation/test-plan.md` ("Quality-gates wiring"): prove pytest+mypy+ruff can actually run on every push/PR, not just locally. Specifically: what triggers does the existing GitHub Actions workflow have; should a new job be added to it or should a separate workflow be created; what env/secrets (if any) does the test job need given all DB/Supabase calls are mocked; and how should `-m "not integration"` be wired so the one real-DB integration test stays skipped in CI.

## Summary

The repository has exactly one GitHub Actions workflow, and it is a **production job, not a CI gate** — it runs the live `dataguard sync` command on a cron schedule and via manual dispatch, and has no `on: push` or `on: pull_request` trigger of any kind. There is no other CI configuration anywhere in the repo (no `.pre-commit-config.yaml`, no second workflow file, no branch-protection-adjacent config visible from the repo contents). Locally, `pytest`, `mypy`, and `ruff` are already dev dependencies with exact commands already documented in `AGENTS.md`, and all three currently pass cleanly against the committed code. Wiring them into CI is a genuinely small, additive task: a new workflow file (kept separate from the production sync workflow, since the two have unrelated triggers, unrelated risk profiles, and one needs production secrets while the other needs none) with a single job running `uv sync`, `uv run ruff check .`, `uv run mypy dataguard/`, and `uv run pytest -m "not integration"`. The integration marker is already registered and the one integration-marked test is automatically excluded by that flag — no new marker plumbing is needed. No environment variables or secrets are required for the test job: `env_check()`, the only code path that reads the four required env vars, is never invoked by any existing test (confirmed by grep — zero test files call `env_check` or drive the CLI through `typer.testing.CliRunner`), so the test suite has no latent dependency on `SOURCE_DB`/`TARGET_DB`/`SUPABASE_URL`/`SUPABASE_KEY` being set.

One correction to the response guidance's framing and one correction to `AGENTS.md`: the response guidance's "should a new job be added to it" question has a clear answer — no, a separate workflow is cleaner given the trigger/secret mismatch (detailed below). Separately, `AGENTS.md` states mypy is "configured in `@pyproject.toml`," but `pyproject.toml` has no `[tool.mypy]` section (or `[tool.ruff]` section) at all — both tools currently run on their stock defaults, not a committed configuration. This doesn't block Phase 1 (the commands work fine as-is) but is worth noting since a CI workflow that pins tool versions makes the absence of pinned tool *configuration* slightly more visible than it is today.

## Detailed Findings

### Existing CI/workflow surface

- The only workflow file in the repo is `.github/workflows/dataguard-sync.yml`. Its triggers are `schedule` (cron, daily) and `workflow_dispatch` (manual) only — no `push` or `pull_request` trigger exists anywhere in the file.
- It runs the real `dataguard sync` command against real `SOURCE_DB`/`TARGET_DB`/`SUPABASE_URL`/`SUPABASE_KEY` secrets, installs the package from a pinned release tag (`uv tool install git+https://github.com/${{ github.repository }}@v0.1.1`) rather than from the working tree, and sets `NO_COLOR: "1"` to keep output machine-readable. This is a production job — it does not build or test the current branch's code at all, since it installs from a fixed tag.
- No other CI-adjacent file exists: no `.pre-commit-config.yaml`, no second `.github/workflows/*.yml`, no `dependabot.yml`. `find . -maxdepth 2 -iname "*.yml" -o -iname "*.yaml"` (excluding `.git/`) returns only the one file.
- `gh` CLI is not available in this environment, so branch-protection rules (which live in GitHub's settings, not repo files) could not be inspected directly. This is a gap `/10x-plan` or the implementer should check manually before assuming a new required check will actually block merges — a CI workflow that runs but isn't marked "required" in branch protection settings gives visibility, not enforcement.

### Existing test/lint/typecheck commands (already documented, verified live)

- `AGENTS.md` documents the exact commands: `uv run pytest`, `uv run mypy dataguard/`, `uv run ruff format . && uv run ruff check --fix .`. All three were re-run directly against the current tree and pass cleanly: `uv run mypy dataguard/` → "Success: no issues found in 7 source files"; the full suite (`uv run pytest -m "not integration"`) → 84 passed, 1 deselected (confirmed earlier in this session); `ruff check .` and `ruff format --check .` → clean.
- `pyproject.toml` has three dedicated sections — `[project]`, `[dependency-groups]` (dev: mypy>=2.1.0, pytest>=9.0.3, ruff>=0.15.15, types-psycopg2), and `[tool.setuptools.packages.find]` — and **no `[tool.mypy]` or `[tool.ruff]` section**. `AGENTS.md`'s line "mypy enforced, configured in `@pyproject.toml`" is only half accurate: mypy is enforced by convention/AGENTS.md, but there is no dedicated mypy configuration block; `[tool.setuptools.packages.find]` is a packaging config, not a mypy one. Both tools currently run on stock defaults, which happen to be clean against the current code.
- A `uv.lock` file is committed (225 KB), so `uv sync` in CI will install the exact pinned dependency graph rather than re-resolving — no separate lockfile-freshness concern for this phase.

### Pytest marker wiring (already correct, nothing new needed)

- `tests/conftest.py` (5 lines) registers the marker: `config.addinivalue_line("markers", "integration: requires a live database")`. This is the only marker-related configuration in the repo — there is no `[tool.pytest.ini_options]` section in `pyproject.toml` (pytest picks up `pyproject.toml` as its rootdir anchor by file presence alone, not because of an ini-options block).
- Exactly one test carries `@pytest.mark.integration`: `test_extract_real_db` in `tests/test_sync.py`, which connects to a real `SOURCE_DB` and is explicitly documented ("Run with: `uv run pytest -m integration`") as opt-in only.
- `uv run pytest -m "not integration"` already correctly excludes it today (verified: "84 passed, 1 deselected" — the 1 deselected is this test). CI just needs to run that exact command; no new marker, no new conftest logic.

### Env var / secret requirements for a test job

- `dataguard/env_check.py`'s `env_check()` is the only function in the codebase that reads `SOURCE_DB`, `TARGET_DB`, `SUPABASE_URL`, `SUPABASE_KEY` from `os.environ` and exits non-zero if any are missing.
- `env_check()` is called from exactly one place: the top of `cli.py`'s `sync()` Typer command (`env_check(); _guard_since_override(since); ...`).
- Grepped `tests/` for `env_check` and for `CliRunner`/`invoke(` (the two ways a test could reach the `sync()` command and therefore `env_check()`): **zero matches for either**. Every existing test calls internal functions directly (`_connect_source`, `run_sync`, `_guard_since_override`, `evaluate`, etc.) — none of the 85 tests ever drives the actual `dataguard sync` CLI entry point end-to-end.
- Conclusion: the current test suite has no latent dependency on the four required env vars. A CI job running `uv run pytest -m "not integration"` needs **no secrets and no `.env` file** to pass, as things stand today. (This will change the moment Phase 4's rollout work — `typer.testing.CliRunner`-based tests per the test plan — starts exercising the real `sync()` command; that phase's own research should re-verify this.)

### Separate workflow vs. extending `dataguard-sync.yml`

The response guidance in `test-plan.md` posed this as an open question. Grounding it: the two jobs have incompatible triggers (cron/manual-dispatch for production runs vs. push/PR for a CI gate), incompatible checkouts (the existing job explicitly installs a *pinned released tag*, not the working tree — a CI gate must test the working tree, specifically the PR's branch), and incompatible secret exposure (a CI job running on every PR from a fork should never need production DB/Supabase credentials; the existing job requires all four). Bolting a test job onto `dataguard-sync.yml` would either force the production job's triggers onto the test gate (wrong — tests should run on every push/PR, not daily) or force the test job to inherit unrelated production secrets. A separate workflow file (e.g. `.github/workflows/ci.yml`) triggered on `push`/`pull_request` is the clean fit, and mirrors the existing file's own already-established pattern for setting up the toolchain (`astral-sh/setup-uv@v5` with `python-version: "3.12"`), which the new workflow should reuse rather than reinvent.

## Code References

- `.github/workflows/dataguard-sync.yml` — https://github.com/agnieszkaka8/dataguard-pipeline/blob/03c252680392ffd054c0fd057a8d4c04ab047057/.github/workflows/dataguard-sync.yml — the only existing workflow; cron + `workflow_dispatch` triggers only, no `push`/`pull_request`; installs a pinned tag, not the working tree; sets `NO_COLOR: "1"`.
- `pyproject.toml` — https://github.com/agnieszkaka8/dataguard-pipeline/blob/03c252680392ffd054c0fd057a8d4c04ab047057/pyproject.toml — dev dependency versions (pytest>=9.0.3, mypy>=2.1.0, ruff>=0.15.15); no `[tool.mypy]` or `[tool.ruff]` section; no `[tool.pytest.ini_options]` section.
- `tests/conftest.py` — https://github.com/agnieszkaka8/dataguard-pipeline/blob/03c252680392ffd054c0fd057a8d4c04ab047057/tests/conftest.py — the sole marker registration (`integration`).
- `tests/test_sync.py` — https://github.com/agnieszkaka8/dataguard-pipeline/blob/03c252680392ffd054c0fd057a8d4c04ab047057/tests/test_sync.py — `test_extract_real_db`, the one `@pytest.mark.integration` test.
- `dataguard/env_check.py` — https://github.com/agnieszkaka8/dataguard-pipeline/blob/03c252680392ffd054c0fd057a8d4c04ab047057/dataguard/env_check.py — the only reader of the four required env vars; sole call site is `cli.py`'s `sync()` command, never reached by any current test.
- `AGENTS.md` — https://github.com/agnieszkaka8/dataguard-pipeline/blob/03c252680392ffd054c0fd057a8d4c04ab047057/AGENTS.md — documents the exact local commands (`uv run pytest`, `uv run mypy dataguard/`, `uv run ruff format . && uv run ruff check --fix .`) a CI workflow should mirror; also the source of the "configured in `@pyproject.toml`" claim corrected above.
- `uv.lock` (repo root, not shown above) — committed; CI should `uv sync` against it rather than re-resolving.

## Architecture Insights

- **Production and CI are cleanly separable by trigger, not by convention alone.** The existing workflow's design (pinned-tag install, cron/dispatch-only, full production secrets) already signals the intended separation — it was simply never paired with a CI counterpart. Phase 1's job is additive, not a refactor of the existing file.
- **The test suite's isolation from `env_check()`/the CLI entry point is a real, load-bearing fact for this phase's cost estimate**, not an assumption: it means Phase 1 can wire a fully green CI gate with zero secret configuration in the repo's GitHub settings. This will not remain true once Phase 4 (per `test-plan.md` §3) adds `CliRunner`-based tests that actually invoke `sync()` — that phase will need to either mock `env_check()` or provide dummy env vars in CI, and should re-verify this via its own research rather than assuming Phase 1's finding still holds.
- **No tool configuration exists to preserve or migrate.** Because `pyproject.toml` has no `[tool.mypy]`/`[tool.ruff]`/`[tool.pytest.ini_options]` sections, wiring CI is purely "run the same three commands AGENTS.md already documents" — there's no risk of CI drifting from a local config that a developer has to keep in sync, because there is no separate config to drift from.

## Historical Context (from prior changes)

- `context/archive/2026-06-05-source-db-extraction/plan.md` §Phase 3 established the `tests/conftest.py` marker convention and the `-m "not integration"` default this phase must replicate in CI — this is the origin of the exact command the new workflow needs to run.
- `context/archive/2026-07-24-live-sync-write-cycle/reviews/impl-review.md` (F1) and `context/archive/2026-08-22-watermark-override/reviews/impl-review.md` (F1) both document real bugs that passed the full local suite (`pytest`+`mypy`+`ruff` all green) and were only caught by a manual `/10x-impl-review` pass, not by CI — because no CI gate has ever existed to run these checks automatically on every change. This is direct historical evidence for *why* Phase 1 matters, independent of the test-plan's abstract "cross-cutting" risk label: the local-only gate has already let two real defects reach a merged, pushed `main` before manual review caught them.
- `context/foundation/test-plan.md` §5 (Quality Gates) already frames both "lint + typecheck" and "unit + integration" as `required after §3 Phase 1` — this research confirms both are cheap to wire (no secrets, no marker plumbing) and finds no blocker to closing that gap immediately.

## Related Research

- None — this is the first research document produced under the `/10x-test-plan` rollout; no other `context/changes/**/research.md` exists yet.

## Open Questions

- **Branch-protection enforcement** could not be verified from repo contents alone (`gh` CLI unavailable in this environment). `/10x-plan` should treat "the workflow runs and reports a status check" and "the check is marked required in GitHub branch protection" as two separate deliverables — the latter requires a GitHub Settings change outside this repo's files, which the implementer should call out explicitly rather than assume is automatic.
- **Workflow file naming/placement** (e.g. `.github/workflows/ci.yml` vs. `test.yml`) is a naming choice with no functional consequence found in research; leaving it to `/10x-plan`.
- **Python version pin for CI** — `pyproject.toml` requires `>=3.12`; the existing workflow already pins `python-version: "3.12"` via `astral-sh/setup-uv@v5`, which is the obvious value to reuse, but confirming there's no reason to test against multiple Python versions (there isn't — `pyproject.toml` has no matrix-relevant constraint) is worth one explicit line in the plan rather than a silent assumption.
