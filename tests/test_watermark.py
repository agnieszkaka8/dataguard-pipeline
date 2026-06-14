from datetime import datetime, timezone
from pathlib import Path

import pytest

from dataguard.watermark import read_watermark, write_watermark


def test_read_watermark_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("dataguard.watermark._WATERMARK_PATH", tmp_path / ".watermark")
    result = read_watermark(None)
    assert result is None


def test_read_watermark_valid_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wm = tmp_path / ".watermark"
    ts = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    wm.write_text(ts.isoformat())
    monkeypatch.setattr("dataguard.watermark._WATERMARK_PATH", wm)
    result = read_watermark(None)
    assert result == ts


def test_read_watermark_corrupted_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wm = tmp_path / ".watermark"
    wm.write_text("not-a-valid-date")
    monkeypatch.setattr("dataguard.watermark._WATERMARK_PATH", wm)
    with pytest.raises(RuntimeError, match="Corrupted .watermark file"):
        read_watermark(None)


def test_read_watermark_since_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --since bypasses .watermark entirely, even if the file is absent
    monkeypatch.setattr("dataguard.watermark._WATERMARK_PATH", tmp_path / ".watermark")
    result = read_watermark("2026-03-01T00:00:00+00:00")
    assert result == datetime(2026, 3, 1, tzinfo=timezone.utc)


def test_write_watermark(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wm = tmp_path / ".watermark"
    monkeypatch.setattr("dataguard.watermark._WATERMARK_PATH", wm)
    ts = datetime(2026, 6, 14, 10, 0, 0, tzinfo=timezone.utc)
    write_watermark(ts)
    assert wm.read_text() == ts.isoformat()
