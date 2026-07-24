from typing import Any

import pytest

from dataguard.models import Outcome
from dataguard.rules import evaluate, validate_rules_schema

# ---------------------------------------------------------------------------
# required
# ---------------------------------------------------------------------------


def test_required_passes_when_field_present() -> None:
    assert evaluate({"age": 30}, [{"field": "age", "check": "required"}]) is None


def test_required_fails_when_field_missing() -> None:
    result = evaluate({}, [{"field": "age", "check": "required"}])
    assert result == (Outcome.INVALID, "age: is required")


def test_required_fails_when_field_none() -> None:
    result = evaluate({"age": None}, [{"field": "age", "check": "required"}])
    assert result == (Outcome.INVALID, "age: is required")


# ---------------------------------------------------------------------------
# type
# ---------------------------------------------------------------------------


def test_type_passes_for_matching_type() -> None:
    cases: list[tuple[Any, str]] = [
        ("hello", "str"),
        (30, "int"),
        (3.5, "float"),
        (True, "bool"),
        (30, "number"),
        (3.5, "number"),
    ]
    for value, type_name in cases:
        rule = [{"field": "x", "check": "type", "value": type_name}]
        assert evaluate({"x": value}, rule) is None, (
            f"{value!r} should pass type={type_name}"
        )


def test_type_fails_for_mismatched_type() -> None:
    result = evaluate(
        {"age": "30"}, [{"field": "age", "check": "type", "value": "int"}]
    )
    assert result == (Outcome.INVALID, "age: expected int, got str")


def test_type_number_excludes_bool() -> None:
    result = evaluate(
        {"age": True}, [{"field": "age", "check": "type", "value": "number"}]
    )
    assert result == (Outcome.INVALID, "age: expected number, got bool")


def test_type_int_excludes_bool() -> None:
    result = evaluate(
        {"age": True}, [{"field": "age", "check": "type", "value": "int"}]
    )
    assert result == (Outcome.INVALID, "age: expected int, got bool")


def test_type_no_coercion_of_numeric_string() -> None:
    result = evaluate(
        {"age": "42"}, [{"field": "age", "check": "type", "value": "int"}]
    )
    assert result == (Outcome.INVALID, "age: expected int, got str")


def test_type_fails_when_field_missing() -> None:
    result = evaluate({}, [{"field": "age", "check": "type", "value": "int"}])
    assert result == (Outcome.INVALID, "age: expected int, got NoneType")


# ---------------------------------------------------------------------------
# comparison operators
# ---------------------------------------------------------------------------


def test_gte_passes_and_fails() -> None:
    rule = [{"field": "age", "check": "gte", "value": 0}]
    assert evaluate({"age": 0}, rule) is None
    assert evaluate({"age": 5}, rule) is None
    assert evaluate({"age": -1}, rule) == (Outcome.INVALID, "age: must be >= 0")


def test_lte_passes_and_fails() -> None:
    rule = [{"field": "age", "check": "lte", "value": 120}]
    assert evaluate({"age": 120}, rule) is None
    assert evaluate({"age": 121}, rule) == (Outcome.INVALID, "age: must be <= 120")


def test_gt_passes_and_fails() -> None:
    rule = [{"field": "age", "check": "gt", "value": 0}]
    assert evaluate({"age": 1}, rule) is None
    assert evaluate({"age": 0}, rule) == (Outcome.INVALID, "age: must be > 0")


def test_lt_passes_and_fails() -> None:
    rule = [{"field": "age", "check": "lt", "value": 120}]
    assert evaluate({"age": 119}, rule) is None
    assert evaluate({"age": 120}, rule) == (Outcome.INVALID, "age: must be < 120")


def test_eq_passes_and_fails() -> None:
    rule = [{"field": "status", "check": "eq", "value": "active"}]
    assert evaluate({"status": "active"}, rule) is None
    assert evaluate({"status": "inactive"}, rule) == (
        Outcome.INVALID,
        "status: must be == active",
    )


def test_ne_passes_and_fails() -> None:
    rule = [{"field": "status", "check": "ne", "value": "banned"}]
    assert evaluate({"status": "active"}, rule) is None
    assert evaluate({"status": "banned"}, rule) == (
        Outcome.INVALID,
        "status: must be != banned",
    )


def test_comparison_errored_on_incompatible_types() -> None:
    rule = [{"field": "age", "check": "gte", "value": 0}]
    result = evaluate({"age": "not-a-number"}, rule)
    assert result == (Outcome.ERRORED, "age: cannot compare str to int")


def test_comparison_errored_when_field_missing() -> None:
    rule = [{"field": "age", "check": "gte", "value": 0}]
    result = evaluate({}, rule)
    assert result == (Outcome.ERRORED, "age: cannot compare NoneType to int")


# ---------------------------------------------------------------------------
# regex
# ---------------------------------------------------------------------------


def test_regex_passes_and_fails() -> None:
    rule = [{"field": "email", "check": "regex", "value": r"^[^@]+@[^@]+\.[^@]+$"}]
    assert evaluate({"email": "a@example.com"}, rule) is None
    assert evaluate({"email": "not-an-email"}, rule) == (
        Outcome.INVALID,
        "email: does not match pattern",
    )


def test_regex_errored_on_non_string_value() -> None:
    rule = [{"field": "email", "check": "regex", "value": r"^[^@]+@[^@]+$"}]
    result = evaluate({"email": [1, 2, 3]}, rule)
    assert result == (Outcome.ERRORED, "email: cannot apply regex to list")


def test_regex_errored_when_field_missing() -> None:
    rule = [{"field": "email", "check": "regex", "value": r"^[^@]+@[^@]+$"}]
    result = evaluate({}, rule)
    assert result == (Outcome.ERRORED, "email: cannot apply regex to NoneType")


# ---------------------------------------------------------------------------
# multi-rule ordering / first-violation-wins
# ---------------------------------------------------------------------------


def test_all_rules_pass_returns_none() -> None:
    rules = [
        {"field": "age", "check": "required"},
        {"field": "age", "check": "type", "value": "int"},
        {"field": "age", "check": "gte", "value": 0},
        {"field": "email", "check": "regex", "value": r"^[^@]+@[^@]+$"},
    ]
    row = {"age": 30, "email": "a@example.com"}
    assert evaluate(row, rules) is None


def test_first_failing_rule_wins() -> None:
    rules = [
        {"field": "age", "check": "required"},
        {"field": "age", "check": "type", "value": "int"},
        {"field": "age", "check": "gte", "value": 0},
    ]
    # age missing entirely -> "required" must fire, not "type" or "gte"
    result = evaluate({}, rules)
    assert result == (Outcome.INVALID, "age: is required")


def test_second_rule_evaluated_when_first_passes() -> None:
    rules = [
        {"field": "age", "check": "required"},
        {"field": "age", "check": "gte", "value": 0},
    ]
    result = evaluate({"age": -5}, rules)
    assert result == (Outcome.INVALID, "age: must be >= 0")


def test_rules_for_unrelated_fields_are_ignored() -> None:
    rules = [{"field": "age", "check": "required"}]
    # row has other fields the rules don't mention — must not affect outcome
    assert evaluate({"age": 30, "extra": "whatever"}, rules) is None


# ---------------------------------------------------------------------------
# validate_rules_schema
# ---------------------------------------------------------------------------


def test_validate_rules_schema_valid_file_returns_rules_list() -> None:
    rules_data = {
        "rules": [
            {"field": "age", "check": "required"},
            {"field": "age", "check": "type", "value": "int"},
            {"field": "age", "check": "gte", "value": 0},
            {"field": "email", "check": "regex", "value": r"^[^@]+@[^@]+$"},
        ]
    }
    assert validate_rules_schema(rules_data) == rules_data["rules"]


def test_validate_rules_schema_required_check_needs_no_value() -> None:
    rules_data = {"rules": [{"field": "age", "check": "required"}]}
    assert validate_rules_schema(rules_data) == rules_data["rules"]


def test_validate_rules_schema_missing_rules_key() -> None:
    with pytest.raises(RuntimeError, match="must be an object with a 'rules' list"):
        validate_rules_schema({})


def test_validate_rules_schema_rules_not_a_list() -> None:
    with pytest.raises(RuntimeError, match="must be an object with a 'rules' list"):
        validate_rules_schema({"rules": {"field": "age"}})


def test_validate_rules_schema_rule_not_an_object() -> None:
    with pytest.raises(RuntimeError, match=r"rule #1 must be an object"):
        validate_rules_schema({"rules": ["not-a-dict"]})


def test_validate_rules_schema_rule_missing_field() -> None:
    with pytest.raises(RuntimeError, match=r"rule #1 missing 'field'"):
        validate_rules_schema({"rules": [{"check": "required"}]})


def test_validate_rules_schema_rule_field_not_a_string() -> None:
    with pytest.raises(RuntimeError, match=r"rule #1 'field' must be a string"):
        validate_rules_schema({"rules": [{"field": 123, "check": "required"}]})


def test_validate_rules_schema_rule_missing_check() -> None:
    with pytest.raises(RuntimeError, match=r"rule #1 missing 'check'"):
        validate_rules_schema({"rules": [{"field": "age"}]})


def test_validate_rules_schema_unknown_check_with_suggestion() -> None:
    with pytest.raises(
        RuntimeError,
        match=r"rule #1 has unknown check 'regexp' \(did you mean 'regex'\?\)",
    ):
        validate_rules_schema(
            {"rules": [{"field": "email", "check": "regexp", "value": "^a$"}]}
        )


def test_validate_rules_schema_unknown_check_no_suggestion() -> None:
    with pytest.raises(RuntimeError, match=r"rule #1 has unknown check 'zzzzz'$"):
        validate_rules_schema({"rules": [{"field": "email", "check": "zzzzz"}]})


def test_validate_rules_schema_non_required_check_missing_value() -> None:
    with pytest.raises(RuntimeError, match=r"rule #1 \('type'\) missing 'value'"):
        validate_rules_schema({"rules": [{"field": "age", "check": "type"}]})


def test_validate_rules_schema_type_check_unknown_type_with_suggestion() -> None:
    with pytest.raises(
        RuntimeError,
        match=r"rule #1 has unknown type 'itn' \(did you mean 'int'\?\)",
    ):
        validate_rules_schema(
            {"rules": [{"field": "age", "check": "type", "value": "itn"}]}
        )


def test_validate_rules_schema_reports_correct_one_based_index() -> None:
    rules_data = {
        "rules": [
            {"field": "age", "check": "required"},
            {"field": "age", "check": "type", "value": "int"},
            {"field": "email", "check": "regexp", "value": "^a$"},
        ]
    }
    with pytest.raises(RuntimeError, match=r"rule #3 has unknown check 'regexp'"):
        validate_rules_schema(rules_data)
