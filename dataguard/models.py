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
