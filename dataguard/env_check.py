import os

import typer
from rich.console import Console

_SUPABASE_VARS: tuple[str, ...] = ("SUPABASE_URL", "SUPABASE_KEY")
_DB_VARS: tuple[str, ...] = ("SOURCE_DB", "TARGET_DB")
_console = Console(no_color=bool(os.environ.get("NO_COLOR")))


def env_check(require_db: bool = False) -> None:
    """Exit non-zero if any required environment variable is missing or empty.

    Prints only variable names, never values — satisfies the no-credential-leakage
    hard rule from AGENTS.md. Must be called before any DB connection attempt.
    require_db=True also checks SOURCE_DB and TARGET_DB (needed for sync only).
    """
    required = _SUPABASE_VARS + (_DB_VARS if require_db else ())
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        _console.print(
            f"[red]Missing required environment variables:[/red] {', '.join(missing)}"
        )
        _console.print(
            "[dim]Copy .env.example to .env and fill in the values, "
            "or set them in your shell / GitHub Secrets.[/dim]"
        )
        raise typer.Exit(code=1)
