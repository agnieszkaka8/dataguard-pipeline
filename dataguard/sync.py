import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.errors
import psycopg2.extensions
from psycopg2 import sql
from rich.console import Console

from dataguard.models import Outcome, RecordResult
from dataguard.watermark import read_watermark

console = Console(no_color=bool(os.environ.get("NO_COLOR")))


def run_sync(
    table: str,
    rules_path: Path,
    dry_run: bool,
    since: str | None,
    timestamp_col: str = "created_at",
) -> list[RecordResult]:
    """Execute the sync pipeline and return per-record results.

    Write order (hard rule): Supabase rejections are written BEFORE
    any valid records are committed to Target DB.
    """
    _source_conn: psycopg2.extensions.connection | None = None
    try:
        console.print("[bold]Connecting[/bold] to Source DB…")
        _source_conn = _connect_source()

        since_dt = read_watermark(since)

        console.print(f"[bold]Extracting[/bold] records from [cyan]{table}[/cyan]…")
        records = _extract(table, since_dt, timestamp_col, _source_conn)
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

            _target_conn = _connect_target()

            if valid:
                console.print(
                    f"[bold]Writing[/bold] {len(valid)} valid record(s) to Target DB…"
                )
                _commit_valid(valid, table, _target_conn)
        else:
            console.print("[yellow]Dry-run mode — no writes performed[/yellow]")

        return results
    finally:
        if _source_conn is not None:
            _source_conn.close()


# ---------------------------------------------------------------------------
# Stubs — each becomes its own module/function as implementation grows
# ---------------------------------------------------------------------------


def _connect_source() -> psycopg2.extensions.connection:
    try:
        return psycopg2.connect(os.environ["SOURCE_DB"])
    except psycopg2.OperationalError as exc:
        raise RuntimeError("Source DB connection failed") from exc


def _connect_target() -> object:
    raise NotImplementedError


def _extract(
    table: str,
    since_dt: datetime | None,
    timestamp_col: str,
    conn: psycopg2.extensions.connection,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        try:
            if since_dt is None:
                query = sql.SQL("SELECT * FROM {t} ORDER BY {c}").format(
                    t=sql.Identifier(table),
                    c=sql.Identifier(timestamp_col),
                )
                cur.execute(query)
            else:
                query = sql.SQL("SELECT * FROM {t} WHERE {c} > %s ORDER BY {c}").format(
                    t=sql.Identifier(table),
                    c=sql.Identifier(timestamp_col),
                )
                cur.execute(query, (since_dt,))
        except psycopg2.errors.UndefinedColumn:
            raise RuntimeError(f"Column '{timestamp_col}' not found in table '{table}'")
        assert cur.description is not None  # always set after a SELECT
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _load_rules(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError("Rules file is not valid JSON") from exc


def _classify(row: dict[str, Any], rules: dict[str, Any]) -> RecordResult:
    return RecordResult(row_id=row.get("id"), outcome=Outcome.VALID)


def _write_rejections_to_supabase(results: list[RecordResult]) -> None:
    raise NotImplementedError


def _commit_valid(results: list[RecordResult], table: str, conn: object) -> None:
    raise NotImplementedError
