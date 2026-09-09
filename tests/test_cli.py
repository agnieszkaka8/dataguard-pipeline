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


# ---------------------------------------------------------------------------
# sync — --rules / --rule-set mutual exclusivity
# ---------------------------------------------------------------------------


def test_sync_requires_exactly_one_of_rules_or_rule_set() -> None:
    with patch("dataguard.cli.console.print") as print_mock:
        with pytest.raises(typer.Exit) as exc_info:
            cli.sync(
                table="orders",
                rules=None,
                rule_set=None,
                dry_run=False,
                since=None,
                timestamp_col="created_at",
            )
    assert exc_info.value.exit_code == 1
    assert "Exactly one of" in print_mock.call_args[0][0]


def test_sync_rejects_both_rules_and_rule_set(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text('{"rules": []}')
    with patch("dataguard.cli.console.print") as print_mock:
        with pytest.raises(typer.Exit) as exc_info:
            cli.sync(
                table="orders",
                rules=rules_path,
                rule_set="orders-v2",
                dry_run=False,
                since=None,
                timestamp_col="created_at",
            )
    assert exc_info.value.exit_code == 1
    assert "Exactly one of" in print_mock.call_args[0][0]


def test_sync_rule_set_path_calls_run_sync_without_file_check() -> None:
    with patch("dataguard.cli.run_sync", return_value=[]) as run_sync_mock:
        cli.sync(
            table="orders",
            rules=None,
            rule_set="orders-v2",
            dry_run=True,
            since=None,
            timestamp_col="created_at",
        )
    run_sync_mock.assert_called_once_with(
        table="orders",
        rules_path=None,
        rule_set_name="orders-v2",
        dry_run=True,
        since=None,
        timestamp_col="created_at",
    )


# ---------------------------------------------------------------------------
# rules subcommand group
# ---------------------------------------------------------------------------


def test_rules_list_prints_no_rule_sets_message_when_empty() -> None:
    with patch("dataguard.cli.rule_sets.list_rule_sets", return_value=[]):
        with patch("dataguard.cli.console.print") as print_mock:
            cli.rules_list()
    assert "No rule sets found" in print_mock.call_args[0][0]


def test_rules_list_prints_table_when_populated() -> None:
    rs = SimpleNamespace(name="orders-v2", table_name="orders", updated_at="x")
    with patch("dataguard.cli.rule_sets.list_rule_sets", return_value=[rs]):
        with patch("dataguard.cli.console.print") as print_mock:
            cli.rules_list()
    print_mock.assert_called_once()


def test_rules_list_runtime_error_exits_nonzero() -> None:
    with patch(
        "dataguard.cli.rule_sets.list_rule_sets",
        side_effect=RuntimeError("boom"),
    ):
        with patch("dataguard.cli.console.print"):
            with pytest.raises(typer.Exit) as exc_info:
                cli.rules_list()
    assert exc_info.value.exit_code == 1


def test_rules_get_not_found_exits_nonzero() -> None:
    with patch(
        "dataguard.cli.rule_sets.get_rule_set",
        side_effect=RuntimeError("Rule set 'missing' not found"),
    ):
        with patch("dataguard.cli.console.print") as print_mock:
            with pytest.raises(typer.Exit) as exc_info:
                cli.rules_get("missing")
    assert exc_info.value.exit_code == 1
    assert "not found" in print_mock.call_args[0][0]


def test_rules_create_missing_file_exits_nonzero(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.json"
    with patch("dataguard.cli.console.print") as print_mock:
        with pytest.raises(typer.Exit) as exc_info:
            cli.rules_create(name="orders-v2", table="orders", from_file=missing)
    assert exc_info.value.exit_code == 1
    assert "Rules file not found" in print_mock.call_args[0][0]


def test_rules_create_success_calls_create_rule_set(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text('{"rules": [{"field": "id", "check": "required"}]}')
    with patch("dataguard.cli.rule_sets.create_rule_set") as create_mock:
        with patch("dataguard.cli.console.print"):
            cli.rules_create(name="orders-v2", table="orders", from_file=rules_path)
    create_mock.assert_called_once_with(
        "orders-v2", "orders", [{"field": "id", "check": "required"}]
    )


def test_rules_create_name_conflict_exits_nonzero(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text('{"rules": []}')
    with patch(
        "dataguard.cli.rule_sets.create_rule_set",
        side_effect=RuntimeError("Rule set 'orders-v2' already exists"),
    ):
        with patch("dataguard.cli.console.print") as print_mock:
            with pytest.raises(typer.Exit) as exc_info:
                cli.rules_create(name="orders-v2", table="orders", from_file=rules_path)
    assert exc_info.value.exit_code == 1
    assert "already exists" in print_mock.call_args[0][0]


def test_rules_update_missing_file_exits_nonzero(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.json"
    with patch("dataguard.cli.console.print") as print_mock:
        with pytest.raises(typer.Exit) as exc_info:
            cli.rules_update(name="orders-v2", from_file=missing)
    assert exc_info.value.exit_code == 1
    assert "Rules file not found" in print_mock.call_args[0][0]


def test_rules_update_success_calls_update_rule_set(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text('{"rules": [{"field": "id", "check": "required"}]}')
    with patch("dataguard.cli.rule_sets.update_rule_set") as update_mock:
        with patch("dataguard.cli.console.print"):
            cli.rules_update(name="orders-v2", from_file=rules_path)
    update_mock.assert_called_once_with(
        "orders-v2", [{"field": "id", "check": "required"}]
    )


def test_rules_delete_confirms_before_deleting() -> None:
    with patch("dataguard.cli.typer.confirm") as confirm_mock:
        with patch("dataguard.cli.rule_sets.delete_rule_set") as delete_mock:
            with patch("dataguard.cli.console.print"):
                cli.rules_delete("orders-v2")
    confirm_mock.assert_called_once_with("Delete rule set 'orders-v2'?", abort=True)
    delete_mock.assert_called_once_with("orders-v2")


def test_rules_delete_not_found_exits_nonzero() -> None:
    with patch("dataguard.cli.typer.confirm"):
        with patch(
            "dataguard.cli.rule_sets.delete_rule_set",
            side_effect=RuntimeError("Rule set 'missing' not found"),
        ):
            with patch("dataguard.cli.console.print") as print_mock:
                with pytest.raises(typer.Exit) as exc_info:
                    cli.rules_delete("missing")
    assert exc_info.value.exit_code == 1
    assert "not found" in print_mock.call_args[0][0]
