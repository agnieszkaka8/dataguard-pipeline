"""Regression guard for the AGENTS.md no-leakage hard rule:

    Connection strings and raw record field values must never appear in
    console output, error messages, or local log files.

A single sentinel value is embedded in fake connection strings and in a
record field, then every console.print call (across cli.py, sync.py,
watermark.py) and every raised exception's str() is swept for it, across
the success path, dry-run, and every failure branch. A generic sweep
(rather than per-call-site assertions) so any future console.print or
exception message added anywhere in those three modules is automatically
covered — see context/foundation/test-plan.md §2 Risk #3 and §3 Phase 2.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import psycopg2
import pytest
import typer

from dataguard import cli

SENTINEL = "SENTINEL-9f3e7c1a"


def _set_fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_DB", f"postgresql://sourceuser:{SENTINEL}@sourcehost/db")
    monkeypatch.setenv("TARGET_DB", f"postgresql://targetuser:{SENTINEL}@targethost/db")
    monkeypatch.setenv("SUPABASE_URL", f"https://{SENTINEL}.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", f"{SENTINEL}-key")


def _capture_console_prints(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    captured: list[str] = []

    def _record(*args: object, **kwargs: object) -> None:
        captured.extend(str(a) for a in args)

    monkeypatch.setattr("dataguard.cli.console.print", _record)
    monkeypatch.setattr("dataguard.sync.console.print", _record)
    monkeypatch.setattr("dataguard.watermark.console.print", _record)
    return captured


def _assert_no_leak(captured: list[str], *exc_chain: BaseException | None) -> None:
    haystacks = list(captured) + [str(exc) for exc in exc_chain if exc is not None]
    for text in haystacks:
        assert SENTINEL not in text, f"no-leakage hard rule violated: {text!r}"


def _write_rules_file(tmp_path: Path, rules: list[dict]) -> Path:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"rules": rules}))
    return rules_path


# ---------------------------------------------------------------------------
# Connection / write failure branches — driver exceptions echo the sentinel
# back, exercising the real sanitizing wrappers in sync.py.
# ---------------------------------------------------------------------------


def test_no_leak_on_source_connect_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fake_env(monkeypatch)
    captured = _capture_console_prints(monkeypatch)
    rules_path = _write_rules_file(tmp_path, [{"field": "age", "check": "required"}])

    def _raise(dsn: str) -> None:
        raise psycopg2.OperationalError(f"could not connect to server: {dsn}")

    monkeypatch.setattr("psycopg2.connect", _raise)

    with pytest.raises(typer.Exit):
        cli.sync(
            table="orders",
            rules=rules_path,
            dry_run=False,
            since=None,
            timestamp_col="created_at",
        )

    _assert_no_leak(captured)


def test_no_leak_on_target_connect_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fake_env(monkeypatch)
    captured = _capture_console_prints(monkeypatch)
    rules_path = _write_rules_file(tmp_path, [{"field": "age", "check": "required"}])
    record = {"id": 1, "age": 30, "email": f"{SENTINEL}@example.com"}
    monkeypatch.setattr("dataguard.sync._extract", lambda *a, **k: [record])

    source_conn = MagicMock()
    calls = {"n": 0}

    def _connect(dsn: str) -> MagicMock:
        calls["n"] += 1
        if calls["n"] == 1:
            return source_conn
        raise psycopg2.OperationalError(f"could not connect to server: {dsn}")

    monkeypatch.setattr("psycopg2.connect", _connect)

    with pytest.raises(typer.Exit):
        cli.sync(
            table="orders",
            rules=rules_path,
            dry_run=False,
            since=None,
            timestamp_col="created_at",
        )

    _assert_no_leak(captured)


def test_no_leak_on_supabase_connect_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fake_env(monkeypatch)
    captured = _capture_console_prints(monkeypatch)
    rules_path = _write_rules_file(tmp_path, [{"field": "age", "check": "required"}])
    record = {"id": 1, "email": f"{SENTINEL}@example.com"}  # missing age -> invalid

    monkeypatch.setattr("dataguard.sync._connect_source", lambda: MagicMock())
    monkeypatch.setattr("dataguard.sync._extract", lambda *a, **k: [record])

    def _raise_create_client(url: str, key: str) -> None:
        raise ValueError(f"bad supabase url {url} key {key}")

    monkeypatch.setattr("dataguard.sync.create_client", _raise_create_client)

    with pytest.raises(typer.Exit):
        cli.sync(
            table="orders",
            rules=rules_path,
            dry_run=False,
            since=None,
            timestamp_col="created_at",
        )

    _assert_no_leak(captured)


def test_no_leak_on_supabase_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fake_env(monkeypatch)
    captured = _capture_console_prints(monkeypatch)
    rules_path = _write_rules_file(tmp_path, [{"field": "age", "check": "required"}])
    record = {"id": 1, "email": f"{SENTINEL}@example.com"}  # missing age -> invalid

    monkeypatch.setattr("dataguard.sync._connect_source", lambda: MagicMock())
    monkeypatch.setattr("dataguard.sync._extract", lambda *a, **k: [record])

    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.side_effect = Exception(
        f"insert failed for row email={SENTINEL}@example.com"
    )
    monkeypatch.setattr("dataguard.sync.create_client", lambda url, key: mock_client)

    with pytest.raises(typer.Exit):
        cli.sync(
            table="orders",
            rules=rules_path,
            dry_run=False,
            since=None,
            timestamp_col="created_at",
        )

    _assert_no_leak(captured)


def test_no_leak_on_target_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fake_env(monkeypatch)
    captured = _capture_console_prints(monkeypatch)
    rules_path = _write_rules_file(tmp_path, [{"field": "age", "check": "required"}])
    record = {"id": 1, "age": 30, "email": f"{SENTINEL}@example.com"}

    monkeypatch.setattr("dataguard.sync._connect_source", lambda: MagicMock())
    monkeypatch.setattr("dataguard.sync._extract", lambda *a, **k: [record])

    target_conn = MagicMock()
    cur = target_conn.cursor.return_value.__enter__.return_value
    cur.executemany.side_effect = psycopg2.Error(
        f"duplicate key value violates unique constraint  "
        f"Detail: Key (email)=({SENTINEL}@example.com) already exists."
    )
    monkeypatch.setattr("dataguard.sync._connect_target", lambda: target_conn)

    with pytest.raises(typer.Exit):
        cli.sync(
            table="orders",
            rules=rules_path,
            dry_run=False,
            since=None,
            timestamp_col="created_at",
        )

    _assert_no_leak(captured)


def test_no_leak_on_corrupted_watermark_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fake_env(monkeypatch)
    captured = _capture_console_prints(monkeypatch)
    rules_path = _write_rules_file(tmp_path, [{"field": "age", "check": "required"}])
    watermark_path = tmp_path / ".watermark"
    watermark_path.write_text(f"not-a-valid-timestamp-{SENTINEL}")

    monkeypatch.setattr("dataguard.watermark._WATERMARK_PATH", watermark_path)
    monkeypatch.setattr("dataguard.sync._connect_source", lambda: MagicMock())

    with pytest.raises(typer.Exit):
        cli.sync(
            table="orders",
            rules=rules_path,
            dry_run=False,
            since=None,
            timestamp_col="created_at",
        )

    _assert_no_leak(captured)


# ---------------------------------------------------------------------------
# Boundary failures that never touch a driver — still swept for completeness.
# ---------------------------------------------------------------------------


def test_no_leak_on_rules_load_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fake_env(monkeypatch)
    captured = _capture_console_prints(monkeypatch)
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(f"{{not valid json {SENTINEL}")

    with pytest.raises(typer.Exit):
        cli.sync(
            table="orders",
            rules=rules_path,
            dry_run=False,
            since=None,
            timestamp_col="created_at",
        )

    _assert_no_leak(captured)


def test_no_leak_on_missing_rules_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fake_env(monkeypatch)
    captured = _capture_console_prints(monkeypatch)
    missing_rules = tmp_path / "does-not-exist.json"

    with pytest.raises(typer.Exit):
        cli.sync(
            table="orders",
            rules=missing_rules,
            dry_run=False,
            since=None,
            timestamp_col="created_at",
        )

    _assert_no_leak(captured)


def test_no_leak_on_invalid_since_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fake_env(monkeypatch)
    captured = _capture_console_prints(monkeypatch)
    rules_path = _write_rules_file(tmp_path, [{"field": "age", "check": "required"}])

    with pytest.raises(typer.Exit):
        cli.sync(
            table="orders",
            rules=rules_path,
            dry_run=False,
            since="not-a-timestamp",
            timestamp_col="created_at",
        )

    _assert_no_leak(captured)


def test_no_leak_on_risky_since_override_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fake_env(monkeypatch)
    captured = _capture_console_prints(monkeypatch)
    watermark_path = tmp_path / ".watermark"
    watermark_path.write_text("2026-06-01T00:00:00+00:00")
    monkeypatch.setattr("dataguard.watermark._WATERMARK_PATH", watermark_path)
    monkeypatch.setattr("dataguard.cli.typer.confirm", lambda *a, **k: True)

    cli._guard_since_override("2026-01-01T00:00:00+00:00")

    _assert_no_leak(captured)


# ---------------------------------------------------------------------------
# Success path and dry-run — no failure, but progress messages must still
# never echo the connection strings or the record's field values.
# ---------------------------------------------------------------------------


def test_no_leak_on_success_path_console_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fake_env(monkeypatch)
    captured = _capture_console_prints(monkeypatch)
    monkeypatch.setattr("dataguard.watermark._WATERMARK_PATH", tmp_path / ".watermark")
    rules_path = _write_rules_file(tmp_path, [{"field": "age", "check": "required"}])
    record = {"id": 1, "age": 30, "email": f"{SENTINEL}@example.com"}

    monkeypatch.setattr("dataguard.sync._connect_source", lambda: MagicMock())
    monkeypatch.setattr("dataguard.sync._extract", lambda *a, **k: [record])
    monkeypatch.setattr("dataguard.sync._connect_target", lambda: MagicMock())
    monkeypatch.setattr("dataguard.sync._commit_valid", MagicMock())
    monkeypatch.setattr("dataguard.sync.write_watermark", MagicMock())

    cli.sync(
        table="orders",
        rules=rules_path,
        dry_run=False,
        since=None,
        timestamp_col="created_at",
    )

    _assert_no_leak(captured)


def test_no_leak_on_dry_run_console_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fake_env(monkeypatch)
    captured = _capture_console_prints(monkeypatch)
    monkeypatch.setattr("dataguard.watermark._WATERMARK_PATH", tmp_path / ".watermark")
    rules_path = _write_rules_file(tmp_path, [{"field": "age", "check": "required"}])
    record = {"id": 1, "age": 30, "email": f"{SENTINEL}@example.com"}

    monkeypatch.setattr("dataguard.sync._connect_source", lambda: MagicMock())
    monkeypatch.setattr("dataguard.sync._extract", lambda *a, **k: [record])

    cli.sync(
        table="orders",
        rules=rules_path,
        dry_run=True,
        since=None,
        timestamp_col="created_at",
    )

    _assert_no_leak(captured)
