import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from dataguard.env_check import env_check
from dataguard.models import Outcome
from dataguard.sync import run_sync

app = typer.Typer()
console = Console(no_color=bool(os.environ.get("NO_COLOR")))


@app.command()
def sync(
    table: str = typer.Option(..., "--table", "-t", help="Source/target table name"),
    rules: Path = typer.Option(..., "--rules", "-r", help="Path to JSON rules file"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate only — no writes"),
    since: Optional[str] = typer.Option(
        None, "--since", help="Override watermark (ISO timestamp)"
    ),
) -> None:
    """Sync records from Source DB to Target DB with validation."""
    env_check()

    if not rules.exists():
        console.print(f"[red]Rules file not found:[/red] {rules}")
        raise typer.Exit(code=1)

    try:
        results = run_sync(table=table, rules_path=rules, dry_run=dry_run, since=since)
    except Exception as exc:
        console.print(f"[red]Sync failed:[/red] {type(exc).__name__}")
        raise typer.Exit(code=1)

    _print_summary(results, dry_run)


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
