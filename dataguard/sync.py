import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.errors
import psycopg2.extensions
from psycopg2 import sql
from rich.console import Console
from supabase import Client, create_client

from dataguard import rule_sets
from dataguard.models import Outcome, RecordResult
from dataguard.rules import evaluate, validate_rules_schema
from dataguard.watermark import read_watermark, write_watermark

console = Console(no_color=bool(os.environ.get("NO_COLOR")))


def run_sync(
    table: str,
    rules_path: Path | None = None,
    dry_run: bool = False,
    since: str | None = None,
    timestamp_col: str = "created_at",
    rule_set_name: str | None = None,
) -> list[RecordResult]:
    """Execute the sync pipeline and return per-record results.

    Write order (hard rule): Supabase rejections are written BEFORE
    any valid records are committed to Target DB.
    """
    _source_conn: psycopg2.extensions.connection | None = None
    _target_conn: psycopg2.extensions.connection | None = None
    try:
        rules = _resolve_rules(table, rules_path, rule_set_name)

        console.print("[bold]Connecting[/bold] to Source DB…")
        _source_conn = _connect_source()

        since_dt = read_watermark(since)

        console.print(f"[bold]Extracting[/bold] records from [cyan]{table}[/cyan]…")
        records = _extract(table, since_dt, timestamp_col, _source_conn)
        console.print(f"  {len(records)} record(s) fetched")

        console.print("[bold]Validating[/bold] records…")
        results = [_classify(row, rules) for row in records]
        pairs = list(zip(records, results))

        invalid_pairs = [(row, r) for row, r in pairs if r.outcome != Outcome.VALID]
        valid_pairs = [(row, r) for row, r in pairs if r.outcome == Outcome.VALID]

        if not dry_run:
            if invalid_pairs:
                console.print(
                    f"[bold]Logging[/bold] {len(invalid_pairs)} rejection(s) to Supabase…"
                )
                _write_rejections_to_supabase(invalid_pairs, table)

            if valid_pairs:
                _target_conn = _connect_target()
                console.print(
                    f"[bold]Writing[/bold] {len(valid_pairs)} valid record(s) to Target DB…"
                )
                _commit_valid(valid_pairs, table, _target_conn)

            now = datetime.now(timezone.utc)
            write_watermark(now)
        else:
            console.print("[yellow]Dry-run mode — no writes performed[/yellow]")

        return results
    finally:
        if _source_conn is not None:
            _source_conn.close()
        if _target_conn is not None:
            _target_conn.close()


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


def _connect_source() -> psycopg2.extensions.connection:
    try:
        return psycopg2.connect(os.environ["SOURCE_DB"])
    except psycopg2.OperationalError as exc:
        raise RuntimeError("Source DB connection failed") from exc


def _connect_target() -> psycopg2.extensions.connection:
    try:
        return psycopg2.connect(os.environ["TARGET_DB"])
    except psycopg2.OperationalError as exc:
        raise RuntimeError("Target DB connection failed") from exc


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


def _load_rules(path: Path) -> list[dict[str, Any]]:
    try:
        rules_data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError("Rules file is not valid JSON") from exc
    return validate_rules_schema(rules_data)


def _resolve_rules(
    table: str, rules_path: Path | None, rule_set_name: str | None
) -> list[dict[str, Any]]:
    """Load rules from a local file or a named Supabase rule set (exactly one)."""
    if rules_path is not None:
        return _load_rules(rules_path)
    if rule_set_name is None:
        raise RuntimeError("Either rules_path or rule_set_name must be provided")

    rule_set = rule_sets.get_rule_set(rule_set_name)
    if rule_set.table_name != table:
        raise RuntimeError(
            f"Rule set '{rule_set_name}' is scoped to table "
            f"'{rule_set.table_name}', not '{table}'"
        )
    return validate_rules_schema({"rules": rule_set.rules})


def _classify(row: dict[str, Any], rules: list[dict[str, Any]]) -> RecordResult:
    row_id = row.get("id")
    result = evaluate(row, rules)
    if result is None:
        return RecordResult(row_id=row_id, outcome=Outcome.VALID)
    outcome, reason = result
    return RecordResult(row_id=row_id, outcome=outcome, reason=reason)


def _json_safe(value: Any) -> str:
    """Fallback encoder for values json.dumps can't serialize natively (datetime, Decimal, UUID, ...)."""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _connect_supabase() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    try:
        return create_client(url, key)
    except Exception as exc:
        raise RuntimeError("Supabase client creation failed") from exc


def _write_rejections_to_supabase(
    invalid_pairs: list[tuple[dict[str, Any], RecordResult]], table: str
) -> None:
    client = _connect_supabase()
    rows = [
        {
            "row_id": result.row_id,
            "table_name": table,
            "outcome": result.outcome.value,
            "reason": result.reason,
            "raw_record": json.loads(json.dumps(row, default=_json_safe)),
        }
        for row, result in invalid_pairs
    ]
    try:
        client.table("rejections").insert(rows).execute()
    except Exception as exc:
        raise RuntimeError(
            "Supabase rejection write failed — aborting, Target DB untouched"
        ) from exc


def _commit_valid(
    valid_pairs: list[tuple[dict[str, Any], RecordResult]],
    table: str,
    conn: psycopg2.extensions.connection,
) -> None:
    if not valid_pairs:
        return
    rows = [row for row, _ in valid_pairs]
    columns = list(rows[0].keys())
    query = sql.SQL("INSERT INTO {t} ({cols}) VALUES ({vals})").format(
        t=sql.Identifier(table),
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        vals=sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    try:
        with conn.cursor() as cur:
            cur.executemany(query, [tuple(row[c] for c in columns) for row in rows])
        conn.commit()
    except Exception as exc:
        raise RuntimeError("Target DB write failed") from exc
