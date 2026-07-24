import operator
import re
from collections.abc import Callable, Iterable
from difflib import get_close_matches
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
    rule that applies to this row's fields, or None if every rule passes.

    Expects `rules` to already be schema-validated (see validate_rules_schema)."""
    for rule in rules:
        field = rule["field"]
        handler = _CHECKS[rule["check"]]
        result = handler(field, row.get(field), rule)
        if result is not None:
            return result
    return None


def _suggest(value: Any, candidates: Iterable[str]) -> str:
    if not isinstance(value, str):
        return ""
    matches = get_close_matches(value, candidates, n=1)
    return f" (did you mean '{matches[0]}'?)" if matches else ""


def validate_rules_schema(rules_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate rules file structure and return the rules list, or raise RuntimeError."""
    if not isinstance(rules_data, dict) or not isinstance(
        rules_data.get("rules"), list
    ):
        raise RuntimeError(
            "Rules file is invalid — top-level JSON must be an object with a 'rules' list"
        )

    rules: list[dict[str, Any]] = rules_data["rules"]
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            raise RuntimeError(
                f"Rules file is invalid — rule #{index} must be an object"
            )

        if "field" not in rule:
            raise RuntimeError(f"Rules file is invalid — rule #{index} missing 'field'")
        if not isinstance(rule["field"], str):
            raise RuntimeError(
                f"Rules file is invalid — rule #{index} 'field' must be a string"
            )

        if "check" not in rule:
            raise RuntimeError(f"Rules file is invalid — rule #{index} missing 'check'")
        check = rule["check"]
        if check not in _CHECKS:
            raise RuntimeError(
                f"Rules file is invalid — rule #{index} has unknown check "
                f"'{check}'{_suggest(check, _CHECKS.keys())}"
            )

        if check != "required" and "value" not in rule:
            raise RuntimeError(
                f"Rules file is invalid — rule #{index} ('{check}') missing 'value'"
            )

        if check == "type" and rule["value"] not in _TYPE_NAMES:
            raise RuntimeError(
                f"Rules file is invalid — rule #{index} has unknown type "
                f"'{rule['value']}'{_suggest(rule['value'], _TYPE_NAMES.keys())}"
            )

        if check == "regex":
            try:
                re.compile(rule["value"])
            except (re.error, TypeError) as exc:
                raise RuntimeError(
                    f"Rules file is invalid — rule #{index} has invalid regex pattern: {exc}"
                ) from exc

    return rules
