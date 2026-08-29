# Quality-Gates Wiring — Plan Brief

> Full plan: `context/changes/testing-quality-gates-wiring/plan.md`
> Research: `context/changes/testing-quality-gates-wiring/research.md`

## What & Why

Add a new GitHub Actions workflow that runs `ruff`, `mypy`, and `pytest -m "not integration"` on every push to `main` and every pull request. Today these three gates are documented in `AGENTS.md` and all pass locally, but the repo's only workflow (`dataguard-sync.yml`) is a production job that never runs them — two real bugs have already slipped past a fully-green local suite and reached `main`, caught only by manual `/10x-impl-review` passes, not CI.

## Starting Point

`.github/workflows/dataguard-sync.yml` exists but is cron/dispatch-only, installs a pinned release tag (not the working tree), and needs 4 production secrets. No `push`/`pull_request`-triggered workflow, `.pre-commit-config.yaml`, or other CI config exists. `pyproject.toml` has no `[tool.mypy]`/`[tool.ruff]`/`[tool.pytest.ini_options]` sections — both tools run on stock defaults, which are clean today.

## Desired End State

A new `.github/workflows/ci.yml` runs all three gates on every push/PR with no secrets required, surfaces all three tools' results on one run (not stop-at-first-failure), and — once manually marked required in GitHub branch protection — actually blocks merging a red PR into `main`.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| CI YAML authorship in this lesson | Proceed and write it | User confirmed the lesson boundary governs `/10x-test-plan`'s guide-writing, not the standard change chain a rollout phase explicitly hands off to | Plan (user decision) |
| New workflow vs. extend existing | Separate `ci.yml` | Incompatible triggers, checkout semantics (pinned tag vs. working tree), and secret exposure vs. `dataguard-sync.yml` | Research |
| Job structure | Single job, sequential steps | Simplest YAML, one dependency install, mirrors the existing workflow's own single-job pattern | Plan (user choice) |
| Failure reporting | Run all 3 tools, aggregate failure at the end | One push surfaces every gate's failures instead of a fix-one-push-again loop | Plan (user choice) |
| Triggers | `push` to `main` + all `pull_request` | Standard small-repo convention; avoids doubled runs from unfiltered push + PR-sync firing on the same commit | Plan (user choice) |
| Secrets | None | No test reaches `env_check()`, the only reader of `SOURCE_DB`/`TARGET_DB`/`SUPABASE_URL`/`SUPABASE_KEY` | Research |
| Branch-protection enforcement | Manual step in this plan's success criteria | Research couldn't verify it from repo files (`gh` unavailable); making it explicit avoids assuming the workflow running is equivalent to it blocking merges | Plan (user choice) |

## Scope

**In scope:** one new workflow file (`.github/workflows/ci.yml`); verifying it triggers, passes, and fails correctly; manually enabling branch-protection enforcement.

**Out of scope:** modifying `dataguard-sync.yml`; adding `[tool.*]` config sections to `pyproject.toml`; multi-Python-version matrix; Phases 2–5 of the test-plan rollout (regression tests, payload coverage, integration coverage, classification protection).

## Architecture / Approach

One additive GitHub Actions workflow, independent of the existing production workflow, mirroring its toolchain-setup steps (`actions/checkout@v4`, `astral-sh/setup-uv@v5` @ Python 3.12) but with its own triggers (push/PR, not cron/dispatch), no secrets, and a single job that runs ruff → mypy → pytest with `continue-on-error` + an aggregating final step so all three gates' results land in one CI run.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Add and verify the CI workflow | `.github/workflows/ci.yml` running all 3 gates on push/PR, plus branch protection enabled | `continue-on-error` misconfigured so a failing step doesn't fail the job overall (silently defeats the gate) |

**Prerequisites:** none — no other phase or infrastructure needed.
**Estimated effort:** ~1 session, single phase.

## Open Risks & Assumptions

- Branch-protection enforcement is a manual GitHub Settings action outside this repo's files; the plan can't automatically verify it was completed.
- Assumes GitHub Actions' default `ubuntu-latest` runner has no compatibility issue with `uv`/Python 3.12 — already implicitly true since `dataguard-sync.yml` uses the same setup successfully today.

## Success Criteria (Summary)

- A push with a genuine lint/type/test violation produces a failing, attributable CI check; a clean push produces a green one.
- Once branch protection is configured, a red PR cannot be merged into `main` through the GitHub UI.
