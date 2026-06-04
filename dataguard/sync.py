from pathlib import Path

from rich.console import Console

from dataguard.models import Outcome, RecordResult

console = Console()


def run_sync(
    table: str,
    rules_path: Path,
    dry_run: bool,
    since: str | None,
) -> list[RecordResult]:
    """Execute the sync pipeline and return per-record results.

    Write order (hard rule): Supabase rejections are written BEFORE
    any valid records are committed to Target DB.
    """
    console.print("[bold]Connecting[/bold] to Source DB and Target DB…")
    _source_conn = _connect_source()
    _target_conn = _connect_target()

    console.print(f"[bold]Extracting[/bold] records from [cyan]{table}[/cyan]…")
    records = _extract(table, since)
    console.print(f"  {len(records)} record(s) fetched")

    rules = _load_rules(rules_path)

    console.print("[bold]Validating[/bold] records…")
    results = [_classify(row, rules) for row in records]

    invalid = [r for r in results if r.outcome != Outcome.VALID]
    valid = [r for r in results if r.outcome == Outcome.VALID]

    if not dry_run:
        if invalid:
            console.print(
                f"[bold]Logging[/bold] {len(invalid)} rejection(s) to Supabase…"
            )
            _write_rejections_to_supabase(invalid)

        if valid:
            console.print(
                f"[bold]Writing[/bold] {len(valid)} valid record(s) to Target DB…"
            )
            _commit_valid(valid, table, _target_conn)
    else:
        console.print("[yellow]Dry-run mode — no writes performed[/yellow]")

    return results


# ---------------------------------------------------------------------------
# Stubs — each becomes its own module/function as implementation grows
# ---------------------------------------------------------------------------


def _connect_source() -> object:
    raise NotImplementedError


def _connect_target() -> object:
    raise NotImplementedError


def _extract(table: str, since: str | None) -> list[dict]:
    return []


def _load_rules(path: Path) -> dict:
    import json

    return json.loads(path.read_text())


def _classify(row: dict, rules: dict) -> RecordResult:
    return RecordResult(row_id=row.get("id"), outcome=Outcome.VALID)


def _write_rejections_to_supabase(results: list[RecordResult]) -> None:
    raise NotImplementedError


def _commit_valid(results: list[RecordResult], table: str, conn: object) -> None:
    raise NotImplementedError
