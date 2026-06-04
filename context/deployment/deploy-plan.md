---
project: dataguard
created: 2026-06-04
platform: GitHub Actions + uv git install
status: ready
---

# DataGuard — Deployment Plan

## Context

Infrastructure decision: **GitHub Actions + `uv tool install git+<repo>@v0.1.0`**.
Source: `context/foundation/infrastructure.md`.

The workflow YAML template in `infrastructure.md` contained six concrete bugs and the
repo was missing six supporting files. This plan documents all corrections in dependency
order.

---

## Gaps Corrected

| # | Gap | Fix |
|---|---|---|
| 1 | `.gitignore` missing `.env` and `.watermark` | Appended both entries |
| 2 | No `.env.example` — engineers had no setup template | Created `.env.example` |
| 3 | No startup env var validation — empty secrets reached psycopg2 silently | Created `dataguard/env_check.py`; called first in `sync()` |
| 4 | No `rules/` directory — workflow referenced `rules/<table>.json` | Created `rules/.gitkeep` |
| 5 | `astral-sh/setup-uv@v1` outdated | Updated to `astral-sh/setup-uv@v5` |
| 6 | Python version not pinned in workflow | Added `python-version: "3.12"` |
| 7 | `NO_COLOR` absent from CI env | Added `NO_COLOR: "1"` to job env block |
| 8 | `--table <table>` was a literal placeholder | Replaced with `matrix.table` strategy |
| 9 | Failure notification was bare `echo` | Kept `::error` annotation; documented email notification path |
| 10 | `uv.lock` not mentioned | Noted: commit `uv.lock` for reproducible installs |
| 11 | `context/deployment/` did not exist | Created this file |

---

## Files Created / Modified

```
.gitignore                               modified — added .env, .watermark, cache dirs
.env.example                             new — 4-var template with comments
dataguard/env_check.py                   new — startup env validation
dataguard/cli.py                         modified — env_check() call added at top of sync()
rules/.gitkeep                           new — tracks rules/ directory in git
.github/workflows/dataguard-sync.yml     new — corrected GitHub Actions workflow
context/deployment/deploy-plan.md        new — this file
```

---

## Corrected Workflow Summary

Key differences from `infrastructure.md` template:

- `astral-sh/setup-uv@v5` with `python-version: "3.12"` (was `@v1`, no python pin)
- `NO_COLOR: "1"` in job-level `env:` block (was absent)
- `matrix.table` strategy replaces `--table <table>` placeholder
- `fail-fast: false` so a failure on one table does not skip others
- `uv tool install git+https://github.com/${{ github.repository }}@v0.1.0` — uses
  `github.repository` context, not a hard-coded username; pinned to `v0.1.0` tag

---

## Setup Steps (one-time, human actions)

### 1. Set GitHub Secrets

```bash
gh secret set SOURCE_DB    --body "postgresql://user:pass@host:5432/db"
gh secret set TARGET_DB    --body "postgresql://user:pass@host:5432/db"
gh secret set SUPABASE_URL --body "https://<project-ref>.supabase.co"
gh secret set SUPABASE_KEY --body "<service-role-key>"
```

Verify: `gh secret list` — all four should appear.

### 2. Add real table name(s) to the workflow matrix

Edit `.github/workflows/dataguard-sync.yml`, replace `orders` under `matrix.table`
with the actual table(s) to sync. Add a matching `rules/<table>.json` for each.

### 3. Initial commit and tag

```bash
git add .gitignore .env.example dataguard/ rules/ .github/ context/deployment/ pyproject.toml uv.lock
git commit -m "chore: initial commit — scaffold + corrected CI workflow"
git tag v0.1.0
git push origin main
git push origin v0.1.0
```

The tag must exist before the first scheduled run — the `uv tool install` step pins to `@v0.1.0`.

---

## Verification

### Local (before push)

```bash
# Install from local source
uv tool install --editable .

# Test: env_check catches missing vars
dataguard sync --dry-run --table orders --rules rules/orders.json
# Expected: exit 1, "Missing required environment variables: SOURCE_DB, TARGET_DB, ..."

# Test: NO_COLOR respected
NO_COLOR=1 dataguard sync --dry-run --table orders --rules rules/orders.json
# Expected: plain text output, no ANSI escape codes

# Type-check and lint
uv run mypy dataguard/ && uv run ruff check dataguard/
```

### CI (after push and secrets set)

```bash
gh workflow run dataguard-sync.yml --ref main
gh run list --workflow=dataguard-sync.yml
gh run view <run-id> --log
```

Success = exit 0, no `::error` annotation, Supabase shows no unexpected new rows.

---

## Ongoing Risks (from infrastructure.md Risk Register)

| Risk | Mitigation |
|---|---|
| GitHub disables schedule after 60 days inactivity | Set a monthly calendar reminder to push a dummy commit or run `gh workflow enable dataguard-sync.yml` |
| Source/Target DB unreachable from GH Actions runners | Verify DB has a public endpoint before scheduling; if firewalled, pivot to Fly.io cron (see `infrastructure.md` option 3) |
| `main` branch broken breaks next cron run | Pin install to `@v0.1.0`; tag new releases explicitly before merging breaking changes |
| No failure email by default | Enable in GitHub Settings → Notifications → Actions → "Send notifications for failed workflows" |
