from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg2
import psycopg2.errors
import pytest

from dataguard.sync import _connect_source, _extract, _load_rules


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
