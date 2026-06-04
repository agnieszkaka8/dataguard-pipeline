---
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
---

## Why this stack

DataGuard is a typed Python CLI tool with a 3-week solo timeline. No registered Python CLI starter exists in the registry; `fastapi` is the closest registered entry and provides the correct Python + uv foundation (pyproject.toml, type-hint culture, uv dependency management). The bootstrapper scaffold should be adapted as follows: **do not install FastAPI or uvicorn**; replace them with `typer` (type-hint-driven CLI framework) and `rich` (color tables, progress bars, console output). This maps directly to FR-004 (real-time console feedback) and FR-005 (color-coded summary table). Add `python-dotenv` for `.env` loading (FR-001), `psycopg2-binary` or `asyncpg` for Source/Target DB connections (FR-003), and `supabase` (Supabase Python SDK) for the rejection log writes (FR-003, Business Logic). The `dataguard sync` command entry point is defined as a Typer app in `pyproject.toml` under `[project.scripts]`. Bootstrapper confidence is `best-effort` because no end-to-end CLI template has been verified in the registry; expect manual scaffold steps.

### Manual scaffold (run after bootstrapper or instead of it)

```bash
uv init dataguard --python 3.12
cd dataguard
uv add typer rich python-dotenv psycopg2-binary supabase
uv add --dev pytest mypy ruff
```

Add to `pyproject.toml`:

```toml
[project.scripts]
dataguard = "dataguard.cli:app"
```

### Alternatives considered

- `go` (starter: `go`) — first-class confidence, single binary, strong CLI idioms; pivot here if Python's DB-driver ecosystem proves frustrating.
- `rust` (starter: `rust`) — verified confidence, maximum correctness guarantees; steeper ramp for a 3-week after-hours timeline.
