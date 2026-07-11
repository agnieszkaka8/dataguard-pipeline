import os
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

console = Console(no_color=bool(os.environ.get("NO_COLOR")))

_WATERMARK_PATH = Path(".watermark")


def read_watermark(since: str | None) -> datetime | None:
    """Return the extraction start point as a UTC-aware datetime.

    Priority: --since override > .watermark file > None (first run).
    Corrupted .watermark is a hard error; missing file is a first-run signal.
    """
    if since is not None:
        return datetime.fromisoformat(since).astimezone(timezone.utc)

    if not _WATERMARK_PATH.exists():
        console.print(
            "[yellow]No .watermark found — processing all records (first run)[/yellow]"
        )
        return None

    try:
        content = _WATERMARK_PATH.read_text().strip()
        return datetime.fromisoformat(content).astimezone(timezone.utc)
    except (ValueError, OSError) as exc:
        raise RuntimeError(
            "Corrupted .watermark file — delete it to start fresh"
        ) from exc


def write_watermark(ts: datetime) -> None:
    """Persist ts as the new watermark (ISO 8601, UTC)."""
    _WATERMARK_PATH.write_text(ts.isoformat())
