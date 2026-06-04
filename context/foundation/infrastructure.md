---
project: dataguard
researched_at: 2026-06-04
recommended_platform: GitHub Actions + uv git install
runner_up: GitHub Actions + PyPI
context_type: mvp
tech_stack:
  language: python
  framework: typer (CLI tool)
  runtime: python 3.12
  package_manager: uv
---

## Recommendation

**Use GitHub Actions for scheduled execution and `uv tool install git+...` for local installation.**

DataGuard is a CLI tool, not a web service — traditional hosting platforms (Vercel, Fly.io, Railway) add cost and operational overhead that a solo project doesn't need yet. GitHub Actions is already the configured CI provider, costs nothing within the free tier, and handles secrets injection natively. Local install via `uv tool install git+https://github.com/<user>/dataguard` requires no PyPI publishing and no Docker image.

## Platform Comparison

| Platform | CLI-first | Managed | Agent docs | Stable API | MCP / Integration | Score |
|---|---|---|---|---|---|---|
| **GitHub Actions + uv git install** | Pass | Pass | Pass | Pass | Partial (GH MCP, beta) | **4.5** |
| GitHub Actions + PyPI | Pass | Pass | Pass | Pass | Partial | **4.5** |
| Fly.io cron | Pass | Pass | Partial | Pass | Pass (flymcp) | **4.5** |
| Railway cron | Pass | Pass | Partial | Pass | Pass | **4.5** |
| Local cron only | Fail | n/a | n/a | Fail | Fail | **2** |

Fly.io and Railway were down-weighted by the cost constraint (minimum $1.94/mo and $5/mo respectively) and the Dockerfile requirement. GitHub-based options tie on raw score but win on cost and zero new-platform overhead.

### Shortlisted Platforms

#### 1. GitHub Actions + uv git install (Recommended)

Completely free within 2,000 min/month (private repos). No new platform to learn — GitHub Actions is already the configured CI provider. Local install: `uv tool install git+https://github.com/<user>/dataguard`. Scheduled execution via `schedule: cron(...)` in the workflow YAML with connection strings injected as GitHub Secrets. No Docker image or PyPI account required.

#### 2. GitHub Actions + PyPI

Same scheduling story as option 1, with the addition of `uv build` + `uv publish` to PyPI on each release. After publishing, any engineer can install with `uv tool install dataguard` (no repo URL required). Adds one publishing step and requires a PyPI account with Trusted Publisher (OIDC) configured. Preferable when team distribution becomes a goal.

#### 3. Fly.io cron

Dedicated cron machine using Fly.io's Machines + Supercronic. Mature `flyctl` CLI (`fly secrets set`, `fly logs`, `fly deploy`). Official Fly.io MCP server for agent-native infra ops. Costs ~$1.94/month for a 256 MB machine running continuously. Requires a Dockerfile to package the Python environment. Best choice if GitHub Actions' public-internet runners can't reach the source/target DBs due to network restrictions.

## Anti-Bias Cross-Check: GitHub Actions + uv git install

### Devil's Advocate — Weaknesses

1. **DB network reachability**: GitHub Actions runners run on the public internet. Source DB and Target DB must accept connections from GitHub's published IP ranges — a non-trivial allowlist that changes periodically. If either DB is behind a VPN or firewall, scheduled runs are a hard blocker.
2. **Silent schedule disablement**: GitHub disables scheduled workflows on repos with no push activity for 60 days. A solo after-hours project will hit this; re-enabling requires a manual visit to the Actions UI.
3. **No pinned version**: `uv tool install git+...` installs HEAD of the default branch. A breaking change to `main` immediately breaks both the local install and the next cron run.
4. **No failure alerting by default**: a failed cron job sends no notification unless a `workflow_run` failure hook or status-check notification is explicitly configured. The sync can fail silently for weeks.
5. **6-hour job timeout**: GitHub Actions kills any job after 6 hours. A very large table sync that exceeds this limit is killed mid-run with no clean abort, potentially leaving a partial transfer.

### Pre-Mortem — How This Could Fail

The engineer deployed DataGuard with a GitHub Actions cron schedule. For weeks it ran cleanly. Then a refactor renamed an internal module and the workflow's `uv run dataguard sync` command started exiting with an import error in under 10 seconds. No failure notification was configured, so the cron appeared to keep "running" — it was just failing fast. The engineer assumed data was flowing. Two weeks later a downstream report showed stale data; investigation traced the gap back to the refactor. The code fix took five minutes; the data backfill with `--since` took hours. The root cause was the absence of a workflow failure notification and the assumption that "it ran" meant "it succeeded." The 60-day inactivity disable also triggered during a holiday period when no commits landed, adding a second silent gap before the engineer noticed the Actions tab showed all jobs as skipped.

### Unknown Unknowns

- **60-day inactivity disable**: GitHub silently disables `schedule:`-triggered workflows on repos with no push activity for 60 days. Solo projects with irregular commit cadence will hit this.
- **`.env` file is never present on the runner**: connection strings must be in GitHub Secrets and explicitly mapped to env vars in the workflow YAML (`env: SOURCE_DB: ${{ secrets.SOURCE_DB }}`). A missing secret arrives as an empty string — not an error — unless the app validates required env vars at startup.
- **`uv tool install git+...` installs HEAD by default**: pin to a tag (`@v0.1.0`) to prevent a broken `main` from propagating to both the local install and the cron runner simultaneously.
- **GitHub IP ranges change**: the allowlist of GitHub Actions runner IPs is published at `https://api.github.com/meta` and updated without notice. DB firewall rules that allowlist this range need periodic review.
- **Private repo minutes**: 2,000 min/month free at 1:1 for Linux runners. If the sync grows (larger tables, slower DBs) and runs multiple times per day, this budget depletes. Beyond the free tier: $0.008/min on Linux.

## Operational Story

- **Preview deploys**: not applicable — DataGuard is a CLI tool, not a web service. Branch testing is done by running `uv run dataguard sync --dry-run` locally against a test DB.
- **Secrets**: DB connection strings (`SOURCE_DB`, `TARGET_DB`, `SUPABASE_URL`, `SUPABASE_KEY`) are stored in GitHub Repository Secrets (Settings → Secrets and variables → Actions). Injected into workflow runs via `env:` block. Never committed to the repo. Rotation: update the secret in the GitHub UI; no redeploy needed.
- **Rollback**: revert the relevant commit on `main`, then `uv tool upgrade dataguard` locally to re-install from the reverted HEAD. For cron runs: the next scheduled execution picks up the reverted code automatically.
- **Approval**: scheduled cron runs execute unattended. Manual runs (`gh workflow run dataguard-sync.yml`) require a human to trigger. Any change to GitHub Secrets (rotating a DB password) requires a human — the agent must not hold write access to repository secrets.
- **Logs**: `gh run list --workflow=dataguard-sync.yml` to list runs; `gh run view <run-id> --log` to tail logs. For local runs, `uv run dataguard sync` outputs directly to the terminal.

## Risk Register

| Risk | Source | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| Source/Target DB unreachable from GH Actions runners | Devil's advocate | M | H | Verify DB public endpoint before scheduling. If firewalled, pivot to Fly.io cron (runner-up option 3). |
| Silent schedule disable after 60 days inactivity | Unknown unknowns | H | M | Add a dummy commit or use `gh workflow enable` in a monthly calendar reminder. |
| `uv tool install git+...` installs broken HEAD | Devil's advocate | M | M | Pin install to a git tag: `uv tool install git+...@v0.1.0`. Tag each stable release. |
| No failure notification for cron jobs | Devil's advocate | H | M | Add `on: workflow_run: workflows: [...] types: [completed]` + Slack/email step, or enable GitHub Actions failure emails in account settings. |
| Empty-string secret silently bypasses validation | Unknown unknowns | M | H | Add startup validation in `cli.py`: check required env vars and exit non-zero if any are missing before connecting to any DB. |
| 6-hour job timeout kills partial sync | Devil's advocate | L | M | Set `timeout-minutes: 300` in the workflow. Add progress checkpointing to `.watermark` so a re-run picks up where it left off. |
| GitHub free-tier minutes exhausted | Unknown unknowns | L | L | Monitor via `gh api /repos/<owner>/dataguard/actions/billing` or GitHub Settings → Billing. |

## Getting Started

1. **Install locally from git** (no PyPI needed):
   ```
   uv tool install git+https://github.com/<your-username>/dataguard
   ```
   Verify: `dataguard --help`

2. **Set up GitHub Secrets** for the scheduled cron job:
   ```
   gh secret set SOURCE_DB --body "postgresql://user:pass@host:5432/db"
   gh secret set TARGET_DB --body "postgresql://user:pass@host:5432/db"
   gh secret set SUPABASE_URL --body "https://<project>.supabase.co"
   gh secret set SUPABASE_KEY --body "<service-role-key>"
   ```

3. **Create the workflow file** at `.github/workflows/dataguard-sync.yml`:
   ```yaml
   name: dataguard-sync
   on:
     schedule:
       - cron: '0 6 * * *'   # daily at 06:00 UTC — adjust as needed
     workflow_dispatch:        # allow manual trigger
   jobs:
     sync:
       runs-on: ubuntu-latest
       timeout-minutes: 300
       env:
         SOURCE_DB: ${{ secrets.SOURCE_DB }}
         TARGET_DB: ${{ secrets.TARGET_DB }}
         SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
         SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
       steps:
         - uses: actions/checkout@v4
         - uses: astral-sh/setup-uv@v1
         - run: uv run dataguard sync --table <table> --rules rules/<table>.json
   ```

4. **Add a failure notification** to the workflow (add after the sync step):
   ```yaml
         - if: failure()
           run: echo "::error::dataguard sync failed — check logs"
   ```
   Or configure email notifications in GitHub account settings (Settings → Notifications → Actions).

5. **Tag your first release** to pin the install:
   ```
   git tag v0.1.0 && git push origin v0.1.0
   ```
   Then reinstall with the pinned tag: `uv tool install git+https://github.com/<user>/dataguard@v0.1.0`

## Out of Scope

The following were not evaluated in this research:
- Docker image configuration
- CI/CD pipeline setup beyond the cron workflow above
- Production-scale architecture (multi-region, HA, DR)
- PyPI publishing workflow (covered in runner-up option if team distribution is needed)
- Self-hosted GitHub Actions runners (relevant if DB firewall blocks public runners)
