import os
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()

from dataguard import auth, rule_sets  # noqa: E402
from dataguard.env_check import env_check  # noqa: E402
from dataguard.models import Outcome  # noqa: E402
from dataguard.sync import _load_rules, run_sync  # noqa: E402
from dataguard.watermark import check_since_override  # noqa: E402

app = typer.Typer(no_args_is_help=True)
rules_app = typer.Typer(no_args_is_help=True)
app.add_typer(rules_app, name="rules", help="Manage named, Supabase-stored rule sets.")
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
    env_check(require_db=ctx.invoked_subcommand == "sync")
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
    rules: Optional[Path] = typer.Option(
        None, "--rules", "-r", help="Path to JSON rules file"
    ),
    rule_set: Optional[str] = typer.Option(
        None, "--rule-set", help="Name of a Supabase-stored rule set"
    ),
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

    if (rules is None) == (rule_set is None):
        console.print(
            "[red]Exactly one of[/red] --rules [red]or[/red] --rule-set "
            "[red]must be provided[/red]"
        )
        raise typer.Exit(code=1)

    if rules is not None and not rules.exists():
        console.print(f"[red]Rules file not found:[/red] {rules}")
        raise typer.Exit(code=1)

    try:
        results = run_sync(
            table=table,
            rules_path=rules,
            rule_set_name=rule_set,
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


@rules_app.command("list")
def rules_list() -> None:
    """List all stored rule sets."""
    try:
        sets = rule_sets.list_rule_sets()
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    if not sets:
        console.print("[yellow]No rule sets found[/yellow]")
        return

    table_out = Table(title="Rule Sets", show_header=True)
    table_out.add_column("Name")
    table_out.add_column("Table")
    table_out.add_column("Last Updated")
    for rule_set in sets:
        table_out.add_row(rule_set.name, rule_set.table_name, rule_set.updated_at)
    console.print(table_out)


@rules_app.command("get")
def rules_get(name: str) -> None:
    """Print a rule set's contents."""
    try:
        rule_set = rule_sets.get_rule_set(name)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print_json(
        data={
            "name": rule_set.name,
            "table": rule_set.table_name,
            "rules": rule_set.rules,
        }
    )


@rules_app.command("create")
def rules_create(
    name: str,
    table: str = typer.Option(..., "--table", help="Table this rule set applies to"),
    from_file: Path = typer.Option(
        ..., "--from-file", help="Path to a local JSON rules file to upload"
    ),
) -> None:
    """Create a new named rule set from a local JSON file."""
    if not from_file.exists():
        console.print(f"[red]Rules file not found:[/red] {from_file}")
        raise typer.Exit(code=1)
    try:
        loaded_rules = _load_rules(from_file)
        rule_sets.create_rule_set(name, table, loaded_rules)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Created[/green] rule set '{name}' for table '{table}'")


@rules_app.command("update")
def rules_update(
    name: str,
    from_file: Path = typer.Option(
        ..., "--from-file", help="Path to a local JSON rules file to upload"
    ),
) -> None:
    """Overwrite a named rule set's contents from a local JSON file."""
    if not from_file.exists():
        console.print(f"[red]Rules file not found:[/red] {from_file}")
        raise typer.Exit(code=1)
    try:
        loaded_rules = _load_rules(from_file)
        rule_sets.update_rule_set(name, loaded_rules)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Updated[/green] rule set '{name}'")


@rules_app.command("delete")
def rules_delete(name: str) -> None:
    """Delete a named rule set."""
    typer.confirm(f"Delete rule set '{name}'?", abort=True)
    try:
        rule_sets.delete_rule_set(name)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Deleted[/green] rule set '{name}'")


if __name__ == "__main__":
    app()
