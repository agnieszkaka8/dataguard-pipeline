import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from dataguard import auth
from dataguard.env_check import env_check
from dataguard.models import Outcome
from dataguard.sync import run_sync
from dataguard.watermark import check_since_override

app = typer.Typer(no_args_is_help=True)
console = Console(no_color=bool(os.environ.get("NO_COLOR")))

_AUTH_ERROR_MESSAGES: dict[str, str] = {
    "invalid_credentials": "invalid email or password",
    "network": "could not reach Supabase — check your network connection",
    "session_revoked": "your session was revoked — please log in again",
}


@app.callback()
def _root(ctx: typer.Context) -> None:
    """DataGuard — validate and sync records between databases."""
    if ctx.invoked_subcommand == "logout":
        # logout must work even with a broken/revoked session or missing env —
        # it is the only escape hatch from an unusable auth state.
        return
    env_check()
    try:
        auth.ensure_session()
    except auth.AuthError as exc:
        console.print(
            f"[red]Authentication failed:[/red] {_AUTH_ERROR_MESSAGES[exc.reason]}"
        )
        raise typer.Exit(code=1)


@app.command()
def logout() -> None:
    """Clear the cached Supabase Auth session."""
    auth.clear_session()
    console.print("[green]Logged out.[/green] Session cache cleared.")


@app.command()
def sync(
    table: str = typer.Option(..., "--table", "-t", help="Source/target table name"),
    rules: Path = typer.Option(..., "--rules", "-r", help="Path to JSON rules file"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate only — no writes"),
    since: Optional[str] = typer.Option(
        None, "--since", help="Override watermark (ISO timestamp)"
    ),
    timestamp_col: str = typer.Option(
        "created_at",
        "--timestamp-col",
        help="Timestamp column used for incremental extraction",
    ),
) -> None:
    """Sync records from Source DB to Target DB with validation."""
    _guard_since_override(since)

    if not rules.exists():
        console.print(f"[red]Rules file not found:[/red] {rules}")
        raise typer.Exit(code=1)

    try:
        results = run_sync(
            table=table,
            rules_path=rules,
            dry_run=dry_run,
            since=since,
            timestamp_col=timestamp_col,
        )
    except Exception as exc:
        console.print(f"[red]Sync failed:[/red] {type(exc).__name__}")
        raise typer.Exit(code=1)

    _print_summary(results, dry_run)


def _guard_since_override(since: Optional[str]) -> None:
    if since is None:
        return
    try:
        risky = check_since_override(since)
    except ValueError:
        console.print(f"[red]Invalid --since timestamp:[/red] {since}")
        raise typer.Exit(code=1)
    if risky:
        console.print(
            f"[bold red]Warning:[/bold red] --since {since} predates the current "
            "watermark. This will re-process already-synced records and may "
            "create duplicates in Target DB."
        )
        typer.confirm("Continue anyway?", abort=True)


def _print_summary(results: list, dry_run: bool) -> None:
    total = len(results)
    passed = sum(1 for r in results if r.outcome == Outcome.VALID)
    failed = sum(1 for r in results if r.outcome == Outcome.INVALID)
    errored = sum(1 for r in results if r.outcome == Outcome.ERRORED)

    label = " [yellow](dry run)[/yellow]" if dry_run else ""
    summary = Table(title=f"Sync Summary{label}", show_header=True)
    summary.add_column("Total", justify="right")
    summary.add_column("Passed", justify="right", style="green")
    summary.add_column("Failed", justify="right", style="red")
    summary.add_column("Errored", justify="right", style="yellow")
    summary.add_row(str(total), str(passed), str(failed), str(errored))

    console.print(summary)

    if failed > 0 or errored > 0:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
