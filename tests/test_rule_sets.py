from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from postgrest.exceptions import APIError

from dataguard import rule_sets
from dataguard.models import RuleSet

# ---------------------------------------------------------------------------
# get_rule_set
# ---------------------------------------------------------------------------


def test_get_rule_set_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = SimpleNamespace(
        data={
            "name": "orders-v2",
            "table_name": "orders",
            "rules": [{"field": "id", "check": "required"}],
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    monkeypatch.setattr(rule_sets, "_client", lambda: client)

    result = rule_sets.get_rule_set("orders-v2")

    assert result == RuleSet(
        name="orders-v2",
        table_name="orders",
        rules=[{"field": "id", "check": "required"}],
        updated_at="2026-01-01T00:00:00Z",
    )


def test_get_rule_set_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = SimpleNamespace(
        data=None
    )
    monkeypatch.setattr(rule_sets, "_client", lambda: client)

    with pytest.raises(RuntimeError, match="not found"):
        rule_sets.get_rule_set("missing")


# ---------------------------------------------------------------------------
# list_rule_sets
# ---------------------------------------------------------------------------


def test_list_rule_sets(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.table.return_value.select.return_value.order.return_value.execute.return_value = SimpleNamespace(
        data=[
            {"name": "a", "table_name": "orders", "rules": [], "updated_at": "x"},
            {"name": "b", "table_name": "users", "rules": [], "updated_at": "y"},
        ]
    )
    monkeypatch.setattr(rule_sets, "_client", lambda: client)

    result = rule_sets.list_rule_sets()

    assert [r.name for r in result] == ["a", "b"]


def test_list_rule_sets_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.table.return_value.select.return_value.order.return_value.execute.return_value = SimpleNamespace(
        data=[]
    )
    monkeypatch.setattr(rule_sets, "_client", lambda: client)

    assert rule_sets.list_rule_sets() == []


# ---------------------------------------------------------------------------
# create_rule_set
# ---------------------------------------------------------------------------


def test_create_rule_set_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    monkeypatch.setattr(rule_sets, "_client", lambda: client)

    rule_sets.create_rule_set(
        "orders-v2", "orders", [{"field": "id", "check": "required"}]
    )

    client.table.return_value.insert.assert_called_once_with(
        {
            "name": "orders-v2",
            "table_name": "orders",
            "rules": [{"field": "id", "check": "required"}],
        }
    )


def test_create_rule_set_name_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.table.return_value.insert.return_value.execute.side_effect = APIError(
        {"message": "duplicate key value violates unique constraint", "code": "23505"}
    )
    monkeypatch.setattr(rule_sets, "_client", lambda: client)

    with pytest.raises(RuntimeError, match="already exists"):
        rule_sets.create_rule_set(
            "orders-v2", "orders", [{"field": "id", "check": "required"}]
        )


def test_create_rule_set_generic_failure_does_not_leak_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.table.return_value.insert.return_value.execute.side_effect = APIError(
        {"message": "SENTINEL-should-not-leak", "code": "500"}
    )
    monkeypatch.setattr(rule_sets, "_client", lambda: client)

    with pytest.raises(RuntimeError) as exc_info:
        rule_sets.create_rule_set(
            "orders-v2", "orders", [{"field": "id", "check": "required"}]
        )
    assert "SENTINEL-should-not-leak" not in str(exc_info.value)


def test_create_rule_set_invalid_rules_never_calls_supabase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    monkeypatch.setattr(rule_sets, "_client", lambda: client)

    with pytest.raises(RuntimeError, match="Rules file is invalid"):
        rule_sets.create_rule_set("bad", "orders", [{"field": "id", "check": "nope"}])
    client.table.assert_not_called()


# ---------------------------------------------------------------------------
# update_rule_set
# ---------------------------------------------------------------------------


def test_update_rule_set_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.table.return_value.update.return_value.eq.return_value.execute.return_value = SimpleNamespace(
        data=[{"name": "orders-v2"}]
    )
    monkeypatch.setattr(rule_sets, "_client", lambda: client)

    rule_sets.update_rule_set("orders-v2", [{"field": "id", "check": "required"}])

    client.table.return_value.update.assert_called_once_with(
        {"rules": [{"field": "id", "check": "required"}]}
    )


def test_update_rule_set_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.table.return_value.update.return_value.eq.return_value.execute.return_value = SimpleNamespace(
        data=[]
    )
    monkeypatch.setattr(rule_sets, "_client", lambda: client)

    with pytest.raises(RuntimeError, match="not found"):
        rule_sets.update_rule_set("missing", [{"field": "id", "check": "required"}])


def test_update_rule_set_invalid_rules_never_calls_supabase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    monkeypatch.setattr(rule_sets, "_client", lambda: client)

    with pytest.raises(RuntimeError, match="Rules file is invalid"):
        rule_sets.update_rule_set("orders-v2", [{"field": "id", "check": "nope"}])
    client.table.assert_not_called()


# ---------------------------------------------------------------------------
# delete_rule_set
# ---------------------------------------------------------------------------


def test_delete_rule_set_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.table.return_value.delete.return_value.eq.return_value.execute.return_value = SimpleNamespace(
        data=[{"name": "orders-v2"}]
    )
    monkeypatch.setattr(rule_sets, "_client", lambda: client)

    rule_sets.delete_rule_set("orders-v2")


def test_delete_rule_set_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.table.return_value.delete.return_value.eq.return_value.execute.return_value = SimpleNamespace(
        data=[]
    )
    monkeypatch.setattr(rule_sets, "_client", lambda: client)

    with pytest.raises(RuntimeError, match="not found"):
        rule_sets.delete_rule_set("missing")
