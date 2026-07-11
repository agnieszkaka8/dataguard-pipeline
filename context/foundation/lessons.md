# Lessons Learned

> Append-only register of recurring rules and patterns. Re-read at start by /10x-frame, /10x-research, /10x-plan, /10x-plan-review, /10x-implement, /10x-impl-review.

## Always pass no_color to Rich Console()

- **Context**: Any `dataguard/` module that instantiates `rich.console.Console`
- **Problem**: `Console()` without `no_color=` violates the project's explicit NO_COLOR convention. AGENTS.md hard rule requires all Rich output to respect `NO_COLOR`. While Rich checks it internally, the project uses explicit `no_color=bool(os.environ.get("NO_COLOR"))` in every module — omitting it in new modules creates inconsistency and breaks the pattern that `/10x-impl-review` checks.
- **Rule**: Always write `console = Console(no_color=bool(os.environ.get("NO_COLOR")))`. Import `os` at module level (never deferred inside a function).
- **Applies to**: All files in `dataguard/` that use Rich console output. First caught during `/10x-impl-review` of `source-db-extraction` (watermark.py and sync.py both omitted it).
