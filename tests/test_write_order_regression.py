"""Regression guard for the AGENTS.md write-order hard rule:

    Supabase rejections are always written before any valid record is
    committed to Target DB, and a Supabase write failure aborts the run
    before Target DB is ever touched.

This file is the sole, dedicated owner of proof for that rule — see
context/foundation/test-plan.md §2 Risk #2 and §3 Phase 2.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from dataguard.sync import run_sync


def _write_rules_file(tmp_path: Path, rules: list[dict]) -> Path:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps({"rules": rules}))
    return rules_path


def test_write_order_rejections_precede_target_commit_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rules_path = _write_rules_file(tmp_path, [{"field": "age", "check": "required"}])
    records = [{"id": 1, "age": 30}, {"id": 2}]
    source_conn = MagicMock()
    target_conn = MagicMock()
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
    monkeypatch.setattr("dataguard.sync.write_watermark", MagicMock())

    run_sync("orders", rules_path, dry_run=False, since="2026-01-01T00:00:00+00:00")

    assert [call[0] for call in manager.mock_calls] == [
        "write_rejections",
        "commit_valid",
    ], "write-order hard rule violated: Target DB must never be written before Supabase"


def test_write_order_aborts_before_target_and_watermark_on_supabase_failure_regression(
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
