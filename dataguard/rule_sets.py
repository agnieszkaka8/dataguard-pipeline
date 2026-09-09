import os
from typing import Any

from postgrest.exceptions import APIError
from rich.console import Console
from supabase import Client, create_client

from dataguard.models import RuleSet
from dataguard.rules import validate_rules_schema

console = Console(no_color=bool(os.environ.get("NO_COLOR")))

_TABLE = "rule_sets"


def _client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    try:
        return create_client(url, key)
    except Exception as exc:
        raise RuntimeError("Supabase client creation failed") from exc


def list_rule_sets() -> list[RuleSet]:
    client = _client()
    try:
        response = client.table(_TABLE).select("*").order("name").execute()
    except Exception as exc:
        raise RuntimeError("Failed to list rule sets") from exc
    return [_to_rule_set(row) for row in response.data]


def get_rule_set(name: str) -> RuleSet:
    client = _client()
    try:
        response = (
            client.table(_TABLE).select("*").eq("name", name).maybe_single().execute()
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch rule set '{name}'") from exc
    if response is None or response.data is None:
        raise RuntimeError(f"Rule set '{name}' not found")
    return _to_rule_set(response.data)


def create_rule_set(name: str, table_name: str, rules: list[dict[str, Any]]) -> None:
    """Validate then insert. Raises RuntimeError on invalid rules, name conflict, or write failure."""
    validate_rules_schema({"rules": rules})
    client = _client()
    try:
        client.table(_TABLE).insert(
            {"name": name, "table_name": table_name, "rules": rules}
        ).execute()
    except APIError as exc:
        if exc.code == "23505":
            raise RuntimeError(f"Rule set '{name}' already exists") from exc
        raise RuntimeError(f"Failed to create rule set '{name}'") from exc
    except Exception as exc:
        raise RuntimeError(f"Failed to create rule set '{name}'") from exc


def update_rule_set(name: str, rules: list[dict[str, Any]]) -> None:
    """Validate then overwrite. Raises RuntimeError on invalid rules, not-found, or write failure."""
    validate_rules_schema({"rules": rules})
    client = _client()
    try:
        response = (
            client.table(_TABLE).update({"rules": rules}).eq("name", name).execute()
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to update rule set '{name}'") from exc
    if not response.data:
        raise RuntimeError(f"Rule set '{name}' not found")


def delete_rule_set(name: str) -> None:
    client = _client()
    try:
        response = client.table(_TABLE).delete().eq("name", name).execute()
    except Exception as exc:
        raise RuntimeError(f"Failed to delete rule set '{name}'") from exc
    if not response.data:
        raise RuntimeError(f"Rule set '{name}' not found")


def _to_rule_set(row: Any) -> RuleSet:
    return RuleSet(
        name=row["name"],
        table_name=row["table_name"],
        rules=row["rules"],
        updated_at=row["updated_at"],
    )
