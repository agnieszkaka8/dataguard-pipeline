import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import psycopg2
import psycopg2.errors
import pytest

from dataguard.models import Outcome, RecordResult
from dataguard.sync import (
    _classify,
    _commit_valid,
    _connect_source,
    _connect_supabase,
    _connect_target,
    _extract,
    _load_rules,
    _write_rejections_to_supabase,
    run_sync,
)


# ---------------------------------------------------------------------------
# _connect_source
# ---------------------------------------------------------------------------


def test_connect_source_success(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_conn = MagicMock()
    monkeypatch.setenv("SOURCE_DB", "postgresql://fake/db")
    with patch("psycopg2.connect", return_value=mock_conn):
        result = _connect_source()
    assert result is mock_conn


def test_connect_source_operational_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_DB", "postgresql://fake/db")

    def _raise(dsn: str) -> None:
        raise psycopg2.OperationalError("connection refused")

    with patch("psycopg2.connect", side_effect=_raise):
        with pytest.raises(RuntimeError, match="Source DB connection failed"):
            _connect_source()


# ---------------------------------------------------------------------------
# _extract helpers
# ---------------------------------------------------------------------------


def _make_mock_conn(rows: list, col_names: list[str]) -> MagicMock:
    """Return a mock psycopg2 connection whose cursor yields the given rows."""
    mock_conn = MagicMock()
    cur = mock_conn.cursor.return_value.__enter__.return_value
    # cursor.description is a sequence of 7-item Column tuples; only name (index 0) matters here
    cur.description = [(name,) + (None,) * 6 for name in col_names]
    cur.fetchall.return_value = rows
    return mock_conn


# ---------------------------------------------------------------------------
# _extract
# ---------------------------------------------------------------------------


def test_extract_all_rows_when_no_watermark() -> None:
    rows = [(1, "Alice"), (2, "Bob")]
    conn = _make_mock_conn(rows, ["id", "name"])
    result = _extract("users", None, "created_at", conn)
    assert result == [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    # With no since_dt the query must NOT contain a WHERE clause
    executed_query = str(
        conn.cursor.return_value.__enter__.return_value.execute.call_args[0][0]
    )
    assert "WHERE" not in executed_query.upper()


def test_extract_filters_by_watermark() -> None:
    rows = [(3, "Carol")]
    conn = _make_mock_conn(rows, ["id", "name"])
    since_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = _extract("users", since_dt, "created_at", conn)
    assert result == [{"id": 3, "name": "Carol"}]
    executed_query = str(
        conn.cursor.return_value.__enter__.return_value.execute.call_args[0][0]
    )
    assert "WHERE" in executed_query.upper()


def test_extract_raises_on_undefined_column() -> None:
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.execute.side_effect = psycopg2.errors.UndefinedColumn(
        "column bad_col does not exist"
    )
    with pytest.raises(
        RuntimeError, match="Column 'bad_col' not found in table 'users'"
    ):
        _extract("users", None, "bad_col", conn)


# ---------------------------------------------------------------------------
# _load_rules
# ---------------------------------------------------------------------------


def test_load_rules_valid_file(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text('{"rules": [{"field": "age", "check": "required"}]}')
    result = _load_rules(rules_path)
    assert result == [{"field": "age", "check": "required"}]


def test_load_rules_invalid_json(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text("{not valid json")
    with pytest.raises(RuntimeError, match="Rules file is not valid JSON"):
        _load_rules(rules_path)


def test_load_rules_malformed_schema_unknown_check(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        '{"rules": [{"field": "email", "check": "regexp", "value": "^a$"}]}'
    )
    with pytest.raises(
        RuntimeError,
        match=r"rule #1 has unknown check 'regexp' \(did you mean 'regex'\?\)",
    ):
        _load_rules(rules_path)


def test_load_rules_malformed_schema_missing_rules_key(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text("{}")
    with pytest.raises(RuntimeError, match="must be an object with a 'rules' list"):
        _load_rules(rules_path)


def test_load_rules_real_orders_json_is_valid() -> None:
    rules_path = Path(__file__).parent.parent / "rules" / "orders.json"
    result = _load_rules(rules_path)
    assert isinstance(result, list)
    assert len(result) > 0
    assert {r["field"] for r in result} == {"id", "customer_email", "amount", "status"}


# ---------------------------------------------------------------------------
# _classify
# ---------------------------------------------------------------------------


def test_classify_valid_record_returns_valid_with_no_reason() -> None:
    rules = [
        {"field": "age", "check": "required"},
        {"field": "age", "check": "type", "value": "int"},
        {"field": "age", "check": "gte", "value": 0},
    ]
    row = {"id": 42, "age": 30}
    result = _classify(row, rules)
    assert result == RecordResult(row_id=42, outcome=Outcome.VALID, reason=None)


def test_classify_invalid_record_returns_invalid_with_reason() -> None:
    rules = [{"field": "age", "check": "required"}]
    row = {"id": 7}
    result = _classify(row, rules)
    assert result == RecordResult(
        row_id=7, outcome=Outcome.INVALID, reason="age: is required"
    )


def test_classify_errored_record_returns_errored_with_reason() -> None:
    rules = [{"field": "email", "check": "regex", "value": r"^[^@]+@[^@]+$"}]
    row = {"id": 9, "email": [1, 2, 3]}
    result = _classify(row, rules)
    assert result == RecordResult(
        row_id=9,
        outcome=Outcome.ERRORED,
        reason="email: cannot apply regex to list",
    )


def test_classify_row_id_taken_regardless_of_outcome() -> None:
    rules = [{"field": "age", "check": "required"}]
    assert _classify({"id": "abc"}, rules).row_id == "abc"
    assert _classify({"id": "abc", "age": 1}, rules).row_id == "abc"


def test_classify_row_id_none_when_id_missing() -> None:
    rules: list[dict] = []
    result = _classify({"age": 1}, rules)
    assert result.row_id is None
    assert result.outcome == Outcome.VALID


# ---------------------------------------------------------------------------
# _connect_supabase
# ---------------------------------------------------------------------------


def test_connect_supabase_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "fake-key")
    mock_client = MagicMock()
    with patch("dataguard.sync.create_client", return_value=mock_client):
        result = _connect_supabase()
    assert result is mock_client


def test_connect_supabase_creation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "not-a-url")
    monkeypatch.setenv("SUPABASE_KEY", "fake-key")
    with patch("dataguard.sync.create_client", side_effect=ValueError("bad url")):
        with pytest.raises(RuntimeError, match="Supabase client creation failed"):
            _connect_supabase()


# ---------------------------------------------------------------------------
# _write_rejections_to_supabase
# ---------------------------------------------------------------------------


def _make_mock_supabase_client() -> MagicMock:
    """Return a mock supabase Client whose .table().insert().execute() chain is inspectable."""
    return MagicMock()


def test_write_rejections_shapes_rows_correctly() -> None:
    client = _make_mock_supabase_client()
    invalid_pairs = [
        (
            {"id": 1, "email": "bad"},
            RecordResult(row_id=1, outcome=Outcome.INVALID, reason="email: invalid"),
        ),
        (
            {"id": 2},
            RecordResult(row_id=2, outcome=Outcome.ERRORED, reason="age: cannot apply"),
        ),
    ]
    with patch("dataguard.sync._connect_supabase", return_value=client):
        _write_rejections_to_supabase(invalid_pairs, "orders")

    client.table.assert_called_once_with("rejections")
    inserted_rows = client.table.return_value.insert.call_args[0][0]
    assert inserted_rows == [
        {
            "row_id": 1,
            "table_name": "orders",
            "outcome": "invalid",
            "reason": "email: invalid",
            "raw_record": {"id": 1, "email": "bad"},
        },
        {
            "row_id": 2,
            "table_name": "orders",
            "outcome": "errored",
            "reason": "age: cannot apply",
            "raw_record": {"id": 2},
        },
    ]


def test_write_rejections_single_batch_call_not_looped() -> None:
    client = _make_mock_supabase_client()
    invalid_pairs = [
        ({"id": i}, RecordResult(row_id=i, outcome=Outcome.INVALID, reason="r"))
        for i in range(5)
    ]
    with patch("dataguard.sync._connect_supabase", return_value=client):
        _write_rejections_to_supabase(invalid_pairs, "orders")

    assert client.table.return_value.insert.call_count == 1
    assert client.table.return_value.insert.return_value.execute.call_count == 1


def test_write_rejections_sanitizes_non_json_native_values() -> None:
    client = _make_mock_supabase_client()
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    invalid_pairs = [
        (
            {"id": 1, "created_at": created_at},
            RecordResult(row_id=1, outcome=Outcome.INVALID, reason="r"),
        ),
    ]
    with patch("dataguard.sync._connect_supabase", return_value=client):
        _write_rejections_to_supabase(invalid_pairs, "orders")

    inserted_rows = client.table.return_value.insert.call_args[0][0]
    assert inserted_rows[0]["raw_record"] == {
        "id": 1,
        "created_at": created_at.isoformat(),
    }
    # Must actually be JSON-serializable end to end, not just equal by luck.
    json.dumps(inserted_rows)


def test_write_rejections_aborts_on_execute_failure() -> None:
    client = _make_mock_supabase_client()
    client.table.return_value.insert.return_value.execute.side_effect = Exception(
        "network error"
    )
    invalid_pairs = [
        ({"id": 1}, RecordResult(row_id=1, outcome=Outcome.INVALID, reason="r"))
    ]
    with patch("dataguard.sync._connect_supabase", return_value=client):
        with pytest.raises(
            RuntimeError,
            match="Supabase rejection write failed — aborting, Target DB untouched",
        ):
            _write_rejections_to_supabase(invalid_pairs, "orders")


# ---------------------------------------------------------------------------
# _connect_target
# ---------------------------------------------------------------------------


def test_connect_target_success(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_conn = MagicMock()
    monkeypatch.setenv("TARGET_DB", "postgresql://fake/db")
    with patch("psycopg2.connect", return_value=mock_conn):
        result = _connect_target()
    assert result is mock_conn


def test_connect_target_operational_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARGET_DB", "postgresql://fake/db")

    def _raise(dsn: str) -> None:
        raise psycopg2.OperationalError("connection refused")

    with patch("psycopg2.connect", side_effect=_raise):
        with pytest.raises(RuntimeError, match="Target DB connection failed"):
            _connect_target()


# ---------------------------------------------------------------------------
# _commit_valid
# ---------------------------------------------------------------------------


def test_commit_valid_dynamic_columns_and_executemany() -> None:
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    valid_pairs = [
        ({"id": 1, "name": "Alice"}, RecordResult(row_id=1, outcome=Outcome.VALID)),
        ({"id": 2, "name": "Bob"}, RecordResult(row_id=2, outcome=Outcome.VALID)),
    ]

    _commit_valid(valid_pairs, "users", conn)

    cur.executemany.assert_called_once()
    query, params = cur.executemany.call_args[0]
    executed_query = str(query).upper()
    assert "INSERT INTO" in executed_query
    assert "USERS" in executed_query
    assert params == [(1, "Alice"), (2, "Bob")]
    conn.commit.assert_called_once()


def test_commit_valid_raises_on_db_error() -> None:
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.executemany.side_effect = psycopg2.Error("constraint violation")
    valid_pairs = [({"id": 1}, RecordResult(row_id=1, outcome=Outcome.VALID))]

    with pytest.raises(RuntimeError, match="Target DB write failed"):
        _commit_valid(valid_pairs, "users", conn)

    conn.commit.assert_not_called()


def test_commit_valid_empty_pairs_is_noop() -> None:
    conn = MagicMock()

    _commit_valid([], "users", conn)

    conn.cursor.assert_not_called()
    conn.commit.assert_not_called()


# ---------------------------------------------------------------------------
# run_sync
# ---------------------------------------------------------------------------


def _write_rules_file(tmp_path: Path, rules: list[dict]) -> Path:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"rules": rules}))
    return rules_path


def test_run_sync_writes_watermark_once_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules_path = _write_rules_file(tmp_path, [{"field": "age", "check": "required"}])
    records = [{"id": 1, "age": 30}, {"id": 2}]
    source_conn = MagicMock()
    target_conn = MagicMock()
    write_watermark_mock = MagicMock()
    write_rejections_mock = MagicMock()
    commit_valid_mock = MagicMock()
    manager = Mock()
    manager.attach_mock(write_rejections_mock, "write_rejections")
    manager.attach_mock(commit_valid_mock, "commit_valid")

    monkeypatch.setattr("dataguard.sync._connect_source", lambda: source_conn)
    monkeypatch.setattr("dataguard.sync._extract", lambda *args, **kwargs: records)
    monkeypatch.setattr("dataguard.sync._connect_target", lambda: target_conn)
    monkeypatch.setattr(
        "dataguard.sync._write_rejections_to_supabase", write_rejections_mock
    )
    monkeypatch.setattr("dataguard.sync._commit_valid", commit_valid_mock)
    monkeypatch.setattr("dataguard.sync.write_watermark", write_watermark_mock)

    run_sync("orders", rules_path, dry_run=False, since="2026-01-01T00:00:00+00:00")

    # Write-order hard rule: rejections logged before Target DB commit.
    assert [call[0] for call in manager.mock_calls] == [
        "write_rejections",
        "commit_valid",
    ]
    write_watermark_mock.assert_called_once()
    source_conn.close.assert_called_once()
    target_conn.close.assert_called_once()


def test_run_sync_writes_watermark_when_all_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules_path = _write_rules_file(tmp_path, [{"field": "age", "check": "required"}])
    records = [{"id": 1}]
    source_conn = MagicMock()
    connect_target_mock = MagicMock()
    write_watermark_mock = MagicMock()

    monkeypatch.setattr("dataguard.sync._connect_source", lambda: source_conn)
    monkeypatch.setattr("dataguard.sync._extract", lambda *args, **kwargs: records)
    monkeypatch.setattr("dataguard.sync._connect_target", connect_target_mock)
    monkeypatch.setattr("dataguard.sync._write_rejections_to_supabase", MagicMock())
    monkeypatch.setattr("dataguard.sync._commit_valid", MagicMock())
    monkeypatch.setattr("dataguard.sync.write_watermark", write_watermark_mock)

    run_sync("orders", rules_path, dry_run=False, since="2026-01-01T00:00:00+00:00")

    connect_target_mock.assert_not_called()
    write_watermark_mock.assert_called_once()


def test_run_sync_dry_run_skips_watermark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules_path = _write_rules_file(tmp_path, [{"field": "age", "check": "required"}])
    records = [{"id": 1, "age": 30}, {"id": 2}]
    source_conn = MagicMock()
    write_watermark_mock = MagicMock()
    write_rejections_mock = MagicMock()
    commit_valid_mock = MagicMock()
    connect_target_mock = MagicMock()

    monkeypatch.setattr("dataguard.sync._connect_source", lambda: source_conn)
    monkeypatch.setattr("dataguard.sync._extract", lambda *args, **kwargs: records)
    monkeypatch.setattr("dataguard.sync._connect_target", connect_target_mock)
    monkeypatch.setattr(
        "dataguard.sync._write_rejections_to_supabase", write_rejections_mock
    )
    monkeypatch.setattr("dataguard.sync._commit_valid", commit_valid_mock)
    monkeypatch.setattr("dataguard.sync.write_watermark", write_watermark_mock)

    run_sync("orders", rules_path, dry_run=True, since="2026-01-01T00:00:00+00:00")

    write_watermark_mock.assert_not_called()
    write_rejections_mock.assert_not_called()
    commit_valid_mock.assert_not_called()
    connect_target_mock.assert_not_called()


def test_run_sync_aborts_before_target_and_watermark_on_supabase_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules_path = _write_rules_file(tmp_path, [{"field": "age", "check": "required"}])
    records = [{"id": 1, "age": 30}, {"id": 2}]
    source_conn = MagicMock()
    connect_target_mock = MagicMock()
    commit_valid_mock = MagicMock()
    write_watermark_mock = MagicMock()

    def _raise_rejection_write(*args: object, **kwargs: object) -> None:
        raise RuntimeError(
            "Supabase rejection write failed — aborting, Target DB untouched"
        )

    monkeypatch.setattr("dataguard.sync._connect_source", lambda: source_conn)
    monkeypatch.setattr("dataguard.sync._extract", lambda *args, **kwargs: records)
    monkeypatch.setattr("dataguard.sync._connect_target", connect_target_mock)
    monkeypatch.setattr(
        "dataguard.sync._write_rejections_to_supabase", _raise_rejection_write
    )
    monkeypatch.setattr("dataguard.sync._commit_valid", commit_valid_mock)
    monkeypatch.setattr("dataguard.sync.write_watermark", write_watermark_mock)

    with pytest.raises(RuntimeError, match="Supabase rejection write failed"):
        run_sync("orders", rules_path, dry_run=False, since="2026-01-01T00:00:00+00:00")

    connect_target_mock.assert_not_called()
    commit_valid_mock.assert_not_called()
    write_watermark_mock.assert_not_called()
    source_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# Integration skeleton (skipped by default — requires SOURCE_DB)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_extract_real_db() -> None:
    """Smoke test: connect to a real Source DB and call _extract.

    Requires SOURCE_DB env var and a table named 'orders' with a 'created_at' column.
    Run with: uv run pytest -m integration
    """
    import os

    real_conn = psycopg2.connect(os.environ["SOURCE_DB"])
    try:
        rows = _extract("orders", None, "created_at", real_conn)
        assert isinstance(rows, list)
    finally:
        real_conn.close()
