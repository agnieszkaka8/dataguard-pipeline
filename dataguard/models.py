from dataclasses import dataclass
from enum import Enum
from typing import Any


class Outcome(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    ERRORED = "errored"


@dataclass
class RecordResult:
    row_id: Any
    outcome: Outcome
    reason: str | None = None


@dataclass
class Session:
    access_token: str
    refresh_token: str
    expires_at: int


@dataclass
class RuleSet:
    name: str
    table_name: str
    rules: list[dict[str, Any]]
    updated_at: str
