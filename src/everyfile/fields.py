"""필드 타입과 셀 단위 변환 규칙.

변환은 셀마다 **규칙 체인**을 남긴다 (`strip:thousands › 괄호→음수 › cast:number`).
미리보기 화면 하단 상태바가 이 체인을 그대로 보여주기 때문에, 변환 결과만 돌려주는
함수로는 부족하다. "왜 이 값이 되었는가" 가 결과값만큼 중요하다.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


class FieldType(StrEnum):
    TEXT = "text"
    CODE = "code"
    """계정코드·사업자번호처럼 숫자로 보이지만 문자열이어야 하는 식별자.

    엑셀 변환에서 가장 자주 터지는 사고가 ``0110`` → ``110`` 이다.
    숫자 타입과 분리해 두면 실수로 캐스팅되는 일을 막을 수 있다.
    """

    INTEGER = "integer"
    DECIMAL = "decimal"
    MONEY = "money"
    """금액. 천단위 콤마, 괄호 음수, ``△`` 표기, 통화기호를 모두 흡수한다."""

    DATE = "date"
    BOOLEAN = "boolean"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    message: str
    severity: Severity = Severity.WARNING


class DiffKind(StrEnum):
    """미리보기 화면에서 셀에 칠할 색을 결정한다."""

    UNCHANGED = "unchanged"
    CHANGED = "changed"
    CREATED = "created"
    """원본이 비어 있었는데 값이 생긴 경우 (기본값 채움 등)."""

    ISSUE = "issue"


@dataclass(slots=True)
class CellResult:
    raw: Any
    value: Any
    """직렬화 대상 파이썬 값. JSON 라이터가 그대로 쓴다."""

    display: str
    """그리드에 찍을 문자열. 값이 None 이면 빈 문자열."""

    rules: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def diff(self) -> DiffKind:
        if self.issues and any(i.severity is not Severity.INFO for i in self.issues):
            return DiffKind.ISSUE
        before = _raw_text(self.raw)
        if not before and self.display:
            return DiffKind.CREATED
        if before != self.display:
            return DiffKind.CHANGED
        return DiffKind.UNCHANGED


@dataclass(slots=True)
class FieldSpec:
    """출력 필드 하나의 정의. 프로파일에 저장·재사용되는 단위."""

    key: str
    """출력 필드명 (JSON 키 / 엑셀 헤더)."""

    source: str
    """원본 헤더명."""

    type: FieldType = FieldType.TEXT
    format: str | None = None
    """DATE 는 strftime 패턴(기본 ``%Y-%m-%d``), MONEY/DECIMAL 은 ``"string"`` 을 주면
    변환 없이 원문을 유지한다."""

    nullable: bool = True
    """빈 값을 ``null`` 로 둘지, 타입별 기본값(``""`` / ``0``)으로 채울지."""

    null_values: tuple[str, ...] = ("", "-", "—", "N/A", "n/a")
    """비어있음으로 취급할 원본 표기. 회계 자료의 ``-`` 는 0이 아니라 '해당 없음'이다."""

    required: bool = False
    """비어 있으면 ERROR 로 표시한다."""

    decimals: int | None = None
    """DECIMAL 반올림 자릿수."""


# ---------------------------------------------------------------------------
# 파싱 보조
# ---------------------------------------------------------------------------

_THOUSANDS = re.compile(r"[,\s ]")
_CURRENCY = re.compile(r"[₩$€¥£원]")
_PAREN_NEG = re.compile(r"^\((.*)\)$")
_TRAILING_NEG = re.compile(r"^(.*?)-$")
_DATE_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_DATE_SEP = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\.?$")
_DATE_KO = re.compile(r"^(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?$")
_DATE_SHORT = re.compile(r"^(\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\.?$")

_TRUE = {"true", "y", "yes", "예", "o", "1", "참"}
_FALSE = {"false", "n", "no", "아니오", "x", "0", "거짓"}


def _raw_text(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, dt.datetime):
        return raw.strftime("%Y-%m-%d") if raw.time() == dt.time(0, 0) else raw.isoformat(" ")
    if isinstance(raw, dt.date):
        return raw.strftime("%Y-%m-%d")
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    return str(raw).strip()


def _is_null(text: str, spec: FieldSpec) -> bool:
    return text in spec.null_values


def parse_number(text: str) -> tuple[Decimal | None, list[str]]:
    """회계 자료의 숫자 표기를 Decimal 로. 규칙 체인을 함께 돌려준다.

    ``(1,234)``, ``△1,234``, ``1,234-``, ``₩1,234`` 를 모두 -1234 / 1234 로 읽는다.
    float 이 아니라 Decimal 인 이유는 금액에서 이진 부동소수 오차를 만들지 않기 위해서다.
    """
    rules: list[str] = []
    s = text
    negative = False

    if _CURRENCY.search(s):
        s = _CURRENCY.sub("", s).strip()
        rules.append("strip:currency")

    m = _PAREN_NEG.match(s)
    if m:
        s = m.group(1).strip()
        negative = True
        rules.append("괄호→음수")

    if s.startswith(("△", "▲")):
        s = s[1:].strip()
        negative = True
        rules.append("△→음수")

    if not negative:
        m = _TRAILING_NEG.match(s)
        if m and m.group(1):
            s = m.group(1).strip()
            negative = True
            rules.append("후행부호→음수")

    if _THOUSANDS.search(s):
        s = _THOUSANDS.sub("", s)
        rules.append("strip:thousands")

    if s.startswith("+"):
        s = s[1:]

    if not s:
        return None, rules

    try:
        value = Decimal(s)
    except InvalidOperation:
        return None, rules

    return (-value if negative else value), rules


def parse_date(text: str) -> tuple[dt.date | None, list[str]]:
    """국내 회계 자료에서 실제로 마주치는 날짜 표기들을 흡수한다."""
    s = text.strip()

    if m := _DATE_COMPACT.match(s):
        return _safe_date(m, "parse:YYYYMMDD")
    if m := _DATE_SEP.match(s):
        return _safe_date(m, "parse:구분자")
    if m := _DATE_KO.match(s):
        return _safe_date(m, "parse:한글표기")
    if m := _DATE_SHORT.match(s):
        y = int(m.group(1))
        # 두 자리 연도: 00~69 는 2000년대, 70~99 는 1900년대 (POSIX 관례)
        year = 2000 + y if y < 70 else 1900 + y
        try:
            return dt.date(year, int(m.group(2)), int(m.group(3))), ["parse:2자리연도"]
        except ValueError:
            return None, []
    return None, []


def _safe_date(m: re.Match[str], rule: str) -> tuple[dt.date | None, list[str]]:
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))), [rule]
    except ValueError:
        return None, []


# ---------------------------------------------------------------------------
# 변환
# ---------------------------------------------------------------------------


def convert_cell(raw: Any, spec: FieldSpec) -> CellResult:
    """원본 셀 하나를 필드 정의에 따라 변환한다."""
    text = _raw_text(raw)

    if _is_null(text, spec):
        return _empty(raw, text, spec)

    handler = {
        FieldType.TEXT: _convert_text,
        FieldType.CODE: _convert_code,
        FieldType.INTEGER: _convert_integer,
        FieldType.DECIMAL: _convert_decimal,
        FieldType.MONEY: _convert_money,
        FieldType.DATE: _convert_date,
        FieldType.BOOLEAN: _convert_boolean,
    }[spec.type]
    return handler(raw, text, spec)


def _empty(raw: Any, text: str, spec: FieldSpec) -> CellResult:
    rules = [f'placeholder:"{text}"'] if text else ["empty"]
    issues: list[Issue] = []
    if spec.required:
        issues.append(
            Issue("REQUIRED_MISSING", f"{spec.key}: 필수 항목이 비어 있습니다", Severity.ERROR)
        )

    if spec.nullable:
        rules.append("→null")
        return CellResult(raw, None, "", rules, issues)

    default: Any = ""
    if spec.type in (FieldType.INTEGER, FieldType.MONEY):
        default = 0
    elif spec.type is FieldType.DECIMAL:
        default = Decimal(0)
    elif spec.type is FieldType.BOOLEAN:
        default = False
    elif spec.type is FieldType.DATE:
        # 날짜에 의미 있는 기본값은 없다. nullable=False 여도 null 을 유지한다.
        rules.append("→null (날짜 기본값 없음)")
        return CellResult(raw, None, "", rules, issues)

    rules.append(f"→{_display(default, spec)!r}" if default != "" else '→""')
    return CellResult(raw, default, _display(default, spec), rules, issues)


def _convert_text(raw: Any, text: str, spec: FieldSpec) -> CellResult:
    return CellResult(raw, text, text, ["trim"])


def _convert_code(raw: Any, text: str, spec: FieldSpec) -> CellResult:
    rules = ["preserve:string"]
    issues: list[Issue] = []
    if text.startswith("0") and text[1:].isdigit():
        rules.append("선행 0 보존")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        # 원본이 이미 숫자 = 엑셀 단계에서 선행 0 이 날아간 뒤다. 되살릴 수 없다.
        issues.append(
            Issue(
                "CODE_READ_AS_NUMBER",
                f"{spec.key}: 원본 셀이 숫자로 저장되어 있어 선행 0 이 남아있지 않을 수 있습니다",
            )
        )
    return CellResult(raw, text, text, rules, issues)


def _convert_integer(raw: Any, text: str, spec: FieldSpec) -> CellResult:
    num, rules = parse_number(text)
    if num is None:
        return _unparsed(raw, text, spec, rules, "정수")
    rules.append("cast:integer")
    issues: list[Issue] = []
    if num != num.to_integral_value():
        issues.append(
            Issue("FRACTION_TRUNCATED", f"{spec.key}: 소수부가 잘렸습니다 ({text})")
        )
    value = int(num.to_integral_value())
    if _lost_leading_zero(text):
        issues.append(
            Issue("LEADING_ZERO_LOST", f"{spec.key}: 선행 0 이 사라집니다 ({text} → {value})")
        )
    return CellResult(raw, value, str(value), rules, issues)


def _convert_decimal(raw: Any, text: str, spec: FieldSpec) -> CellResult:
    num, rules = parse_number(text)
    if num is None:
        return _unparsed(raw, text, spec, rules, "소수")
    if spec.decimals is not None:
        num = num.quantize(Decimal(1).scaleb(-spec.decimals))
        rules.append(f"round:{spec.decimals}")
    rules.append("cast:decimal")
    return CellResult(raw, num, format(num, "f"), rules)


def _convert_money(raw: Any, text: str, spec: FieldSpec) -> CellResult:
    if spec.format == "string":
        return CellResult(raw, text, text, ["passthrough"])
    num, rules = parse_number(text)
    if num is None:
        return _unparsed(raw, text, spec, rules, "금액")
    rules.append("cast:number")
    value: Any = int(num) if num == num.to_integral_value() else num
    return CellResult(raw, value, format(num, "f"), rules)


def _convert_date(raw: Any, text: str, spec: FieldSpec) -> CellResult:
    fmt = spec.format or "%Y-%m-%d"

    if isinstance(raw, dt.datetime):
        date, rules = raw.date(), ["native:datetime"]
    elif isinstance(raw, dt.date):
        date, rules = raw, ["native:date"]
    else:
        date, rules = parse_date(text)

    if date is None:
        return _unparsed(raw, text, spec, rules, "날짜")

    out = date.strftime(fmt)
    rules.append(f"format:{'ISO-8601' if fmt == '%Y-%m-%d' else fmt}")
    return CellResult(raw, out, out, rules)


def _convert_boolean(raw: Any, text: str, spec: FieldSpec) -> CellResult:
    if isinstance(raw, bool):
        return CellResult(raw, raw, "true" if raw else "false", ["native:bool"])
    low = text.lower()
    if low in _TRUE:
        return CellResult(raw, True, "true", ["cast:boolean"])
    if low in _FALSE:
        return CellResult(raw, False, "false", ["cast:boolean"])
    return _unparsed(raw, text, spec, [], "참/거짓")


def _unparsed(
    raw: Any, text: str, spec: FieldSpec, rules: list[str], label: str
) -> CellResult:
    """변환 실패. 값을 버리지 않고 원문을 그대로 들고 이슈로 표시한다.

    조용히 null 로 만들면 감사 대상 자료에서 데이터가 소리 없이 사라진다.
    """
    rules = [*rules, "실패:원문 유지"]
    issue = Issue(
        "PARSE_FAILED",
        f"{spec.key}: {label}(으)로 읽을 수 없습니다 — {text!r}",
        Severity.ERROR,
    )
    return CellResult(raw, text, text, rules, [issue])


def _lost_leading_zero(text: str) -> bool:
    stripped = _THOUSANDS.sub("", text)
    return len(stripped) > 1 and stripped.startswith("0") and stripped[1:].isdigit()


def _display(value: Any, spec: FieldSpec) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)
