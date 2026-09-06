from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import typer

from dataguard import auth, cli
from dataguard.cli import _guard_since_override


# ---------------------------------------------------------------------------
# _root callback — auth gate
# ---------------------------------------------------------------------------


def test_root_skips_auth_and_env_check_for_logout() -> None:
    ctx = SimpleNamespace(invoked_subcommand="logout")
    with patch("dataguard.cli.env_check") as env_check_mock:
        with patch("dataguard.cli.auth.ensure_session") as ensure_mock:
            cli._root(ctx)
    env_check_mock.assert_not_called()
    ensure_mock.assert_not_called()


def test_root_runs_env_check_and_auth_for_other_commands() -> None:
    ctx = SimpleNamespace(invoked_subcommand="sync")
    with patch("dataguard.cli.env_check") as env_check_mock:
        with patch("dataguard.cli.auth.ensure_session") as ensure_mock:
            cli._root(ctx)
    env_check_mock.assert_called_once()
    ensure_mock.assert_called_once()


def test_root_auth_error_prints_message_and_exits_nonzero() -> None:
    ctx = SimpleNamespace(invoked_subcommand="sync")
    with patch("dataguard.cli.env_check"):
        with patch(
            "dataguard.cli.auth.ensure_session",
            side_effect=auth.AuthError("invalid_credentials"),
        ):
            with patch("dataguard.cli.console.print") as print_mock:
                with pytest.raises(typer.Exit) as exc_info:
                    cli._root(ctx)
    assert exc_info.value.exit_code == 1
    print_mock.assert_called_once()
    assert "invalid email or password" in print_mock.call_args[0][0]


# ---------------------------------------------------------------------------
# logout command
# ---------------------------------------------------------------------------


def test_logout_clears_session_and_prints_confirmation() -> None:
    with patch("dataguard.cli.auth.clear_session") as clear_mock:
        with patch("dataguard.cli.console.print") as print_mock:
            cli.logout()
    clear_mock.assert_called_once()
    print_mock.assert_called_once()


def test_guard_since_override_noop_when_since_is_none() -> None:
    with patch("dataguard.cli.check_since_override") as check_mock:
        _guard_since_override(None)
    check_mock.assert_not_called()


def test_guard_since_override_noop_when_not_risky() -> None:
    with patch("dataguard.cli.check_since_override", return_value=False):
        with patch("dataguard.cli.typer.confirm") as confirm_mock:
            _guard_since_override("2026-01-01T00:00:00+00:00")
    confirm_mock.assert_not_called()


def test_guard_since_override_warns_and_confirms_when_risky() -> None:
    with patch("dataguard.cli.check_since_override", return_value=True):
        with patch("dataguard.cli.console.print") as print_mock:
            with patch("dataguard.cli.typer.confirm") as confirm_mock:
                _guard_since_override("2026-01-01T00:00:00+00:00")
    print_mock.assert_called_once()
    assert "2026-01-01T00:00:00+00:00" in print_mock.call_args[0][0]
    confirm_mock.assert_called_once_with("Continue anyway?", abort=True)


def test_guard_since_override_propagates_abort_on_decline() -> None:
    with patch("dataguard.cli.check_since_override", return_value=True):
        with patch("dataguard.cli.console.print"):
            with patch("dataguard.cli.typer.confirm", side_effect=typer.Abort()):
                with pytest.raises(typer.Abort):
                    _guard_since_override("2026-01-01T00:00:00+00:00")


def test_guard_since_override_invalid_since_exits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # check_since_override is not mocked here — exercises the real ValueError path.
    monkeypatch.setattr("dataguard.watermark._WATERMARK_PATH", tmp_path / ".watermark")
    with patch("dataguard.cli.console.print") as print_mock:
        with pytest.raises(typer.Exit):
            _guard_since_override("not-a-timestamp")
    print_mock.assert_called_once()
    assert "Invalid --since timestamp" in print_mock.call_args[0][0]
