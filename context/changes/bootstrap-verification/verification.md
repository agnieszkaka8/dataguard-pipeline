---
bootstrapped_at: 2026-05-31T15:43:00Z
starter_id: fastapi
starter_name: FastAPI
project_name: dataguard
language_family: python
package_manager: uv
cwd_strategy: native-cwd
bootstrapper_confidence: best-effort
phase_3_status: ok
audit_command: pip-audit
---

## Hand-off

```yaml
starter_id: fastapi
package_manager: uv
project_name: dataguard
hints:
  language_family: python
  team_size: solo
  deployment_target: local
  ci_provider: github-actions
  ci_default_flow: auto-deploy-on-merge
  bootstrapper_confidence: best-effort
  path_taken: custom
  quality_override: false
  self_check_answers:
    typed: true
    from_official_starter: true
    conventions: true
    docs_current: true
    can_judge_agent: false
  has_auth: false
  has_payments: false
  has_realtime: false
  has_ai: false
  has_background_jobs: false
```

### Why this stack

DataGuard is a typed Python CLI tool with a 3-week solo timeline. No registered Python CLI starter exists in the registry; `fastapi` is the closest registered entry and provides the correct Python + uv foundation (pyproject.toml, type-hint culture, uv dependency management). The bootstrapper scaffold should be adapted as follows: **do not install FastAPI or uvicorn**; replace them with `typer` (type-hint-driven CLI framework) and `rich` (color tables, progress bars, console output). This maps directly to FR-004 (real-time console feedback) and FR-005 (color-coded summary table). Add `python-dotenv` for `.env` loading (FR-001), `psycopg2-binary` or `asyncpg` for Source/Target DB connections (FR-003), and `supabase` (Supabase Python SDK) for the rejection log writes (FR-003, Business Logic). The `dataguard sync` command entry point is defined as a Typer app in `pyproject.toml` under `[project.scripts]`. Bootstrapper confidence is `best-effort` because no end-to-end CLI template has been verified in the registry; expect manual scaffold steps.

## Pre-scaffold verification

| Signal      | Value    | Severity | Notes                                                                 |
| ----------- | -------- | -------- | --------------------------------------------------------------------- |
| npm package | not run  | n/a      | python starter — no npm package to check                              |
| GitHub repo | not run  | n/a      | docs_url is https://fastapi.tiangolo.com (not a GitHub URL); skipped |

## Scaffold log

**Resolved invocation**:
```
# Step 1: uv not found in PATH — installed via Astral installer (curl -LsSf https://astral.sh/uv/install.sh | sh)
# Step 2: user selected manual scaffold (replacing fastapi/uvicorn with typer/rich/etc. per hand-off)
uv init . --name dataguard --python 3.12
uv add typer rich python-dotenv psycopg2-binary supabase
uv add --dev pytest mypy ruff
uv pip install -e .
# pyproject.toml [project.scripts] and [tool.setuptools.packages.find] added manually
# dataguard/__init__.py and dataguard/cli.py created as CLI stubs
```

**Strategy**: native-cwd (scaffolded directly into the current directory)

**Exit code**: 0 (all commands)

**Pre-flight files-to-touch**: pyproject.toml, .python-version, .gitignore, .python-version, main.py, README.md, uv.lock, .venv/ (best-effort enumeration)

**Files written by CLI**: 8 (pyproject.toml, .python-version, .gitignore, main.py, README.md, uv.lock, dataguard/__init__.py, dataguard/cli.py)

**Pre-existing files preserved**: CLAUDE.md, context/, .claude/, .agents/, skills-lock.json

**Note — uv side effect**: `uv init` also ran `git init` and created `.git/` in the project root. A fresh git repository is now initialized. No git history was present before this run.

**Note — deviation from cmd_template**: the registry's raw `cmd_template` for fastapi (`uv init {name} && uv add fastapi uvicorn`) was not used. Per user selection at Step 0, the manual scaffold commands from the hand-off's "Why this stack" section were used instead. FastAPI and uvicorn were not installed.

## Post-scaffold audit

**Tool**: pip-audit 2.10.0

**Summary**: 0 CRITICAL, 0 HIGH, 0 MODERATE, 0 LOW

**Direct vs transitive**: not distinguished by pip-audit

**Notes**: 66 packages audited. 1 skipped (`dataguard 0.1.0` — local package not on PyPI, expected). Clean tree.

## Hints recorded but not acted on

| Hint                    | Value                                                                                        |
| ----------------------- | -------------------------------------------------------------------------------------------- |
| bootstrapper_confidence | best-effort (hand-off override; registry rates fastapi as first-class for API use case)      |
| quality_override        | false                                                                                        |
| path_taken              | custom                                                                                       |
| self_check_answers      | typed: true, from_official_starter: true, conventions: true, docs_current: true, can_judge_agent: false |
| team_size               | solo                                                                                         |
| deployment_target       | local                                                                                        |
| ci_provider             | github-actions                                                                               |
| ci_default_flow         | auto-deploy-on-merge                                                                         |
| has_auth                | false                                                                                        |
| has_payments            | false                                                                                        |
| has_realtime            | false                                                                                        |
| has_ai                  | false                                                                                        |
| has_background_jobs     | false                                                                                        |

## Next steps

Next: a future skill will set up agent context (CLAUDE.md, AGENTS.md). For now, your project is scaffolded and verified — happy hacking.

Useful manual steps in the meantime:
- A `.git/` repo was created automatically by `uv init`. You are already in a git repository — no `git init` needed.
- `main.py` is a stub created by `uv init`. It can be deleted or repurposed once `dataguard/cli.py` is your entry point.
- Run `uv run dataguard --help` to verify the CLI entry point works (confirmed during bootstrap).
- Review `dataguard/cli.py` — the `sync` command is a stub; implementation follows from the change planning workflow (`/10x-new`, `/10x-plan`, `/10x-implement`).
- Address audit findings per your project's risk tolerance — the full breakdown is in this log (0 findings; nothing to address).
