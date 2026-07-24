import operator
import re
from collections.abc import Callable
from typing import Any

from dataguard.models import Outcome

RuleResult = tuple[Outcome, str] | None
_RuleHandler = Callable[[str, Any, dict[str, Any]], RuleResult]

_TYPE_NAMES: dict[str, type | tuple[type, ...]] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "number": (int, float),
}


def _check_required(field: str, value: Any, rule: dict[str, Any]) -> RuleResult:
    if value is None:
        return (Outcome.INVALID, f"{field}: is required")
    return None


def _check_type(field: str, value: Any, rule: dict[str, Any]) -> RuleResult:
    type_name = rule["value"]
    if type_name == "number":
        if type(value) is bool or type(value) not in (int, float):
            return (
                Outcome.INVALID,
                f"{field}: expected number, got {type(value).__name__}",
            )
        return None

    expected = _TYPE_NAMES[type_name]
    if type(value) is not expected:
        return (
            Outcome.INVALID,
            f"{field}: expected {type_name}, got {type(value).__name__}",
        )
    return None


def _make_comparison_check(op: Callable[[Any, Any], bool], symbol: str) -> _RuleHandler:
    def handler(field: str, value: Any, rule: dict[str, Any]) -> RuleResult:
        target = rule["value"]
        try:
            passed = op(value, target)
        except TypeError:
            return (
                Outcome.ERRORED,
                f"{field}: cannot compare {type(value).__name__} to {type(target).__name__}",
            )
        if not passed:
            return (Outcome.INVALID, f"{field}: must be {symbol} {target}")
        return None

    return handler


def _check_regex(field: str, value: Any, rule: dict[str, Any]) -> RuleResult:
    pattern = rule["value"]
    try:
        matched = re.search(pattern, value)
    except TypeError:
        return (
            Outcome.ERRORED,
            f"{field}: cannot apply regex to {type(value).__name__}",
        )
    if matched is None:
        return (Outcome.INVALID, f"{field}: does not match pattern")
    return None


_CHECKS: dict[str, _RuleHandler] = {
    "required": _check_required,
    "type": _check_type,
    "gte": _make_comparison_check(operator.ge, ">="),
    "lte": _make_comparison_check(operator.le, "<="),
    "gt": _make_comparison_check(operator.gt, ">"),
    "lt": _make_comparison_check(operator.lt, "<"),
    "eq": _make_comparison_check(operator.eq, "=="),
    "ne": _make_comparison_check(operator.ne, "!="),
    "regex": _check_regex,
}


def evaluate(row: dict[str, Any], rules: list[dict[str, Any]]) -> RuleResult:
    """Return (Outcome.INVALID | Outcome.ERRORED, reason) for the first failing
    rule that applies to this row's fields, or None if every rule passes."""
    for rule in rules:
        field = rule["field"]
        handler = _CHECKS[rule["check"]]
        result = handler(field, row.get(field), rule)
        if result is not None:
            return result
    return None
