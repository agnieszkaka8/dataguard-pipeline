# Watermark Override Warning (S-04) Implementation Plan

## Overview

`--since` already exists as a CLI flag and `read_watermark()` already honors it as an override — this plan adds the one piece FR-008 still requires: when `--since` predates the currently stored `.watermark`, print a prominent warning and require explicit acknowledgment before the sync proceeds, since the sync would otherwise silently re-process already-transferred records and risk duplicates in Target DB.

## Current State Analysis

- `dataguard/cli.py:27-29` already exposes `--since` (`Optional[str]`) and passes it straight through to `run_sync()` — no comparison against the stored watermark happens anywhere.
- `dataguard/watermark.py:12-33` `read_watermark(since)` returns the parsed override immediately when `since is not None` ([watermark.py:18-19](../../../dataguard/watermark.py#L18-L19)), bypassing the `.watermark` file entirely — there is no code path that ever reads *both* the override and the stored value in the same call.
- `tests/test_watermark.py` covers `read_watermark`'s four existing behaviors (missing file, valid file, corrupted file, since-override) with a `monkeypatch.setattr("dataguard.watermark._WATERMARK_PATH", ...)` pattern this plan reuses.
- No `tests/test_cli.py` exists yet — CLI-level logic in this codebase is otherwise untested directly; existing precedent (`tests/test_sync.py`) is to unit-test the underlying function rather than the CLI entry point.

## Desired End State

Running `dataguard sync --since <timestamp> ...` when `<timestamp>` is earlier than the current `.watermark` value prints a prominent warning naming the duplicate-record risk and pauses for an interactive yes/no confirmation; declining aborts with a non-zero exit code before any DB connection is attempted. When `<timestamp>` is at or after the stored watermark, or no `.watermark` file exists yet, the sync proceeds exactly as it does today — no behavior change, no extra prompt.

**Verification**: `uv run pytest -m "not integration"` passes covering the new comparison logic and the CLI confirmation gate; `uv run mypy dataguard/` passes; a manual run with `--since` set earlier than an existing `.watermark` shows the warning and blocks on decline, proceeds on confirm.

### Key Discoveries:

- PRD FR-008 (`context/foundation/prd.md:86-87`) specifies the resolution exactly: "the tool will print a prominent warning when `--since` predates the existing watermark, requiring explicit acknowledgment before proceeding. The duplication risk is owned by the engineer." This is a manual-invocation feature (testing/recovery) — the scheduled cron path (`.github/workflows/dataguard-sync.yml`) never passes `--since`, so an interactive prompt does not block automation.
- `typer.confirm(..., abort=True)` is the idiomatic Typer mechanism for exactly this: prints the prompt, raises `typer.Abort` on decline, which Typer turns into a non-zero exit — no manual `typer.Exit` handling needed.

## What We're NOT Doing

- Not adding a `--force`/`--yes` flag to bypass the prompt non-interactively — PRD's resolution calls for "explicit acknowledgment," and no automation path in this repo passes `--since`, so there's no need for a non-interactive override yet.
- Not changing `read_watermark()`'s public contract or its four already-tested behaviors — the refactor here is internal (extracting a private helper), not a signature or behavior change.
- Not persisting or logging the override decision anywhere — the warning + confirm is a one-time gate for that invocation only.

## Implementation Approach

Extract `read_watermark`'s file-reading branch into a private `_read_stored_watermark()` helper (same corrupted-file hard-error behavior, no console printing), so both `read_watermark` and a new `check_since_override()` can read the stored value without duplicating parsing logic. `check_since_override(since)` returns `True` when the override predates the stored watermark. `cli.py` calls it right after `env_check()` and, if `True`, prints the warning and gates on `typer.confirm(..., abort=True)`.

## Phase 1: Watermark comparison logic

### Overview

Refactor `read_watermark`'s file-read branch into a reusable private helper, then add `check_since_override()` on top of it.

### Changes Required:

#### 1. Tests (written first)

**File**: `tests/test_watermark.py`

**Intent**: Cover `check_since_override`'s three cases — no stored watermark (not risky), `since` at/after stored watermark (not risky), `since` before stored watermark (risky) — plus confirm a corrupted `.watermark` file still hard-errors through this path. Reuse the existing `monkeypatch.setattr("dataguard.watermark._WATERMARK_PATH", ...)` pattern.

**Contract**: New `# --- check_since_override ---` section with `test_check_since_override_no_stored_watermark`, `test_check_since_override_since_at_or_after_stored`, `test_check_since_override_since_before_stored`, `test_check_since_override_corrupted_watermark_raises`.

#### 2. `dataguard/watermark.py` — extract helper, add comparison

**File**: `dataguard/watermark.py`

**Intent**: Factor the existing missing/corrupted/valid file-read logic out of `read_watermark` into `_read_stored_watermark() -> datetime | None` (no console output — that stays the caller's job), then add `check_since_override(since: str) -> bool` on top of it.

**Contract**:
```python
def _read_stored_watermark() -> datetime | None:
    if not _WATERMARK_PATH.exists():
        return None
    try:
        content = _WATERMARK_PATH.read_text().strip()
        return datetime.fromisoformat(content).astimezone(timezone.utc)
    except (ValueError, OSError) as exc:
        raise RuntimeError(
            "Corrupted .watermark file — delete it to start fresh"
        ) from exc


def read_watermark(since: str | None) -> datetime | None:
    if since is not None:
        return datetime.fromisoformat(since).astimezone(timezone.utc)
    stored = _read_stored_watermark()
    if stored is None:
        console.print(
            "[yellow]No .watermark found — processing all records (first run)[/yellow]"
        )
    return stored


def check_since_override(since: str) -> bool:
    """Return True if `since` predates the stored watermark (duplicate-record risk)."""
    since_dt = datetime.fromisoformat(since).astimezone(timezone.utc)
    stored = _read_stored_watermark()
    if stored is None:
        return False
    return since_dt < stored
```
The four existing `read_watermark` tests must keep passing unchanged — this preserves identical external behavior and console messages.

### Success Criteria:

#### Automated Verification:

- [ ] All watermark tests pass: `uv run pytest tests/test_watermark.py -v -m "not integration"`
- [ ] Type checking passes: `uv run mypy dataguard/`
- [ ] Linting passes: `uv run ruff check . && uv run ruff format --check .`
- [ ] Full suite passes: `uv run pytest -m "not integration"`

#### Manual Verification:

- [ ] None — this phase is pure logic with no user-facing surface; Phase 2's manual checks cover the end-to-end behavior.

---

## Phase 2: CLI warning and confirmation gate

### Overview

Wire `check_since_override` into the `sync` command: warn and require confirmation before proceeding when the override is risky.

### Changes Required:

#### 1. Tests (written first)

**File**: `tests/test_cli.py` (new)

**Intent**: Unit-test the new gate function directly (mirrors this codebase's existing preference for testing functions over full CLI invocation) rather than driving the whole Typer app.

**Contract**: New file with `_guard_since_override` tests: not called at all when `since is None`; no prompt when `check_since_override` returns `False`; warning printed and `typer.confirm` invoked when `True`; declining the confirm (mock raises `typer.Abort`) propagates the abort.

#### 2. `dataguard/cli.py` — warning + confirmation gate

**File**: `dataguard/cli.py`

**Intent**: Add a small `_guard_since_override(since)` helper called from `sync()` right after `env_check()`, before the rules-file-exists check — keeps the pre-condition checks grouped together, ahead of any DB connection attempt.

**Contract**:
```python
def _guard_since_override(since: Optional[str]) -> None:
    if since is not None and check_since_override(since):
        console.print(
            f"[bold red]Warning:[/bold red] --since {since} predates the current "
            "watermark. This will re-process already-synced records and may "
            "create duplicates in Target DB."
        )
        typer.confirm("Continue anyway?", abort=True)
```
Import `check_since_override` from `dataguard.watermark` alongside the existing imports. Call `_guard_since_override(since)` as the first statement inside `sync()` after `env_check()`.

### Success Criteria:

#### Automated Verification:

- [ ] All CLI tests pass: `uv run pytest tests/test_cli.py -v -m "not integration"`
- [ ] Type checking passes: `uv run mypy dataguard/`
- [ ] Linting passes: `uv run ruff check . && uv run ruff format --check .`
- [ ] Full suite passes: `uv run pytest -m "not integration"`

#### Manual Verification:

- [ ] Run `dataguard sync --since <timestamp-before-existing-watermark> --table orders --rules rules/orders.json --dry-run`; confirm the warning prints and declining the prompt aborts before any "Connecting to Source DB" message.
- [ ] Same command, confirm accepting proceeds normally into the sync.
- [ ] Run with `--since <timestamp-at-or-after-watermark>`; confirm no warning/prompt appears.
- [ ] Run with `--since` and no `.watermark` file present (first run); confirm no warning/prompt appears.

---

## Testing Strategy

### Unit Tests:

- `check_since_override`: no stored watermark, since at/after stored, since before stored, corrupted stored file.
- `_guard_since_override`: skipped when `since is None`, skipped when not risky, warns + confirms when risky, propagates abort on decline.
- Regression: all four pre-existing `read_watermark` tests continue to pass unchanged after the extraction.

### Integration Tests:

- None added — this is a CLI-prompt feature; Phase 2's manual verification covers the real interactive path, matching S-03's precedent of unit tests + manual steps for user-facing behavior.

### Manual Testing Steps:

1. With an existing `.watermark` file, run `--since` set to a timestamp before it; confirm the warning appears and decline aborts (non-zero exit, no DB connection attempted).
2. Repeat, this time confirming; sync proceeds normally.
3. Run `--since` at/after the stored watermark; confirm no prompt.
4. Run `--since` with no `.watermark` file present; confirm no prompt (first run has nothing to duplicate against).

## Performance Considerations

None — this adds a single file read and an interactive prompt gated behind an already-optional flag; no impact on the sync hot path.

## Migration Notes

None — no schema or data changes; `.watermark` file format is unchanged.

## References

- Roadmap: `context/foundation/roadmap.md` § S-04 (PRD ref FR-008, prerequisite S-03 — done)
- PRD: `context/foundation/prd.md:86-87` — FR-008 and its Socrates resolution (the exact requirement this plan implements)
- `dataguard/watermark.py:12-33` — `read_watermark`, refactored in Phase 1
- `dataguard/cli.py:22-55` — `sync` command, wired in Phase 2
- `tests/test_watermark.py` — existing test patterns this plan extends

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Watermark comparison logic

#### Automated

- [x] 1.1 All watermark tests pass: `uv run pytest tests/test_watermark.py -v -m "not integration"` — 42a42f5
- [x] 1.2 Type checking passes: `uv run mypy dataguard/` — 42a42f5
- [x] 1.3 Linting passes: `uv run ruff check . && uv run ruff format --check .` — 42a42f5
- [x] 1.4 Full suite passes: `uv run pytest -m "not integration"` — 42a42f5

### Phase 2: CLI warning and confirmation gate

#### Automated

- [x] 2.1 All CLI tests pass: `uv run pytest tests/test_cli.py -v -m "not integration"`
- [x] 2.2 Type checking passes: `uv run mypy dataguard/`
- [x] 2.3 Linting passes: `uv run ruff check . && uv run ruff format --check .`
- [x] 2.4 Full suite passes: `uv run pytest -m "not integration"`

#### Manual

- [ ] 2.5 Warning appears and decline aborts before any DB connection when `--since` predates `.watermark`
- [ ] 2.6 Confirming the prompt proceeds normally
- [ ] 2.7 No prompt when `--since` is at/after the stored watermark
- [ ] 2.8 No prompt when no `.watermark` file exists yet
