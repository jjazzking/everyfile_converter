"""셀 단위 변환 규칙."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from everyfile.fields import (
    DiffKind,
    FieldSpec,
    FieldType,
    Severity,
    convert_cell,
    parse_date,
    parse_number,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234", Decimal(1234)),
        ("(1,240,000)", Decimal(-1240000)),
        ("△1,234", Decimal(-1234)),
        ("▲500", Decimal(-500)),
        ("1,234-", Decimal(-1234)),
        ("₩ 34,500,000", Decimal(34500000)),
        ("1234원", Decimal(1234)),
        ("0", Decimal(0)),
        ("12.34", Decimal("12.34")),
        ("+500", Decimal(500)),
    ],
)
def test_parses_accounting_number_notations(text, expected):
    """괄호 음수, △, 후행부호, 통화기호를 모두 흡수한다."""
    value, _ = parse_number(text)
    assert value == expected


@pytest.mark.parametrize("text", ["", "미지급", "N/A", "-", "1,2,3,4a"])
def test_returns_none_for_non_numbers(text):
    assert parse_number(text)[0] is None


def test_number_rules_are_reported():
    """상태바에 보여줄 규칙 체인이 실제로 남는다."""
    _, rules = parse_number("(1,240,000)")
    assert rules == ["괄호→음수", "strip:thousands"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("20260115", dt.date(2026, 1, 15)),
        ("2026-01-15", dt.date(2026, 1, 15)),
        ("2026/1/5", dt.date(2026, 1, 5)),
        ("2026.01.15.", dt.date(2026, 1, 15)),
        ("2026년 1월 15일", dt.date(2026, 1, 15)),
        ("26-01-15", dt.date(2026, 1, 15)),
        ("99.12.31", dt.date(1999, 12, 31)),
    ],
)
def test_parses_date_notations(text, expected):
    assert parse_date(text)[0] == expected


@pytest.mark.parametrize("text", ["20261301", "2026-02-30", "그저께", ""])
def test_rejects_impossible_dates(text):
    assert parse_date(text)[0] is None


def test_code_preserves_leading_zero():
    """계정코드는 문자열로 유지된다 — 이 프로젝트에서 가장 중요한 불변조건."""
    spec = FieldSpec(key="accountCode", source="계정코드", type=FieldType.CODE)
    result = convert_cell("0110", spec)
    assert result.value == "0110"
    assert result.display == "0110"
    assert "선행 0 보존" in result.rules
    assert result.diff is DiffKind.UNCHANGED


def test_integer_type_flags_leading_zero_loss():
    """숫자로 캐스팅하면 값이 바뀐다는 사실을 조용히 넘기지 않는다."""
    spec = FieldSpec(key="accountCode", source="계정코드", type=FieldType.INTEGER)
    result = convert_cell("0110", spec)
    assert result.value == 110
    assert result.diff is DiffKind.ISSUE
    assert [i.code for i in result.issues] == ["LEADING_ZERO_LOST"]


def test_code_warns_when_source_was_numeric():
    """원본이 이미 숫자면 선행 0 은 복구 불가 — 경고해야 한다."""
    spec = FieldSpec(key="accountCode", source="계정코드", type=FieldType.CODE)
    result = convert_cell(110, spec)
    assert [i.code for i in result.issues] == ["CODE_READ_AS_NUMBER"]


def test_money_paren_becomes_negative():
    spec = FieldSpec(key="debit", source="차변", type=FieldType.MONEY)
    result = convert_cell("(240,000)", spec)
    assert result.value == -240000
    assert result.rules == ["괄호→음수", "strip:thousands", "cast:number"]


def test_money_string_format_keeps_original():
    spec = FieldSpec(key="debit", source="차변", type=FieldType.MONEY, format="string")
    result = convert_cell("(240,000)", spec)
    assert result.value == "(240,000)"
    assert result.diff is DiffKind.UNCHANGED


def test_dash_is_null_not_zero():
    """회계 자료의 ``-`` 는 0이 아니라 '해당 없음'이다."""
    spec = FieldSpec(key="counterparty", source="거래처", type=FieldType.TEXT)
    result = convert_cell("-", spec)
    assert result.value is None
    assert result.rules == ['placeholder:"-"', "→null"]


def test_non_nullable_money_defaults_to_zero():
    spec = FieldSpec(key="credit", source="대변", type=FieldType.MONEY, nullable=False)
    result = convert_cell("", spec)
    assert result.value == 0
    assert result.diff is DiffKind.CREATED


def test_required_empty_is_an_error():
    spec = FieldSpec(key="entryDate", source="전표일자", type=FieldType.DATE, required=True)
    result = convert_cell("", spec)
    assert result.issues[0].severity is Severity.ERROR
    assert result.issues[0].code == "REQUIRED_MISSING"


def test_unparsable_value_is_kept_not_dropped():
    """변환 실패 시 값을 버리면 감사 자료에서 데이터가 소리 없이 사라진다."""
    spec = FieldSpec(key="debit", source="차변", type=FieldType.MONEY)
    result = convert_cell("확인요망", spec)
    assert result.value == "확인요망"
    assert result.issues[0].code == "PARSE_FAILED"
    assert result.issues[0].severity is Severity.ERROR


def test_date_formats_to_iso_by_default():
    spec = FieldSpec(key="entryDate", source="전표일자", type=FieldType.DATE)
    result = convert_cell("20260115", spec)
    assert result.value == "2026-01-15"
    assert result.rules == ["parse:YYYYMMDD", "format:ISO-8601"]


def test_date_honours_custom_format():
    spec = FieldSpec(key="entryDate", source="전표일자", type=FieldType.DATE, format="%Y/%m/%d")
    assert convert_cell("20260115", spec).value == "2026/01/15"


def test_native_excel_datetime_is_used_directly():
    spec = FieldSpec(key="entryDate", source="전표일자", type=FieldType.DATE)
    result = convert_cell(dt.datetime(2026, 1, 15, 0, 0), spec)
    assert result.value == "2026-01-15"
    assert "native:datetime" in result.rules


def test_decimal_rounds_to_configured_places():
    spec = FieldSpec(key="rate", source="환율", type=FieldType.DECIMAL, decimals=2)
    result = convert_cell("1,234.5678", spec)
    assert result.value == Decimal("1234.57")
