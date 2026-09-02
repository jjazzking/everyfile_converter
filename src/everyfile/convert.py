"""IR + 프로파일 → 변환 결과.

결과는 값만이 아니라 **셀마다 규칙 체인과 이슈, 그리고 원본 행 번호**를 함께 들고 있다.
라이터는 값만 꺼내 쓰고, 미리보기 화면은 나머지를 전부 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .fields import CellResult, DiffKind, Issue, Severity, convert_cell
from .ir import RowKind, TableIR
from .profile import Profile


@dataclass(slots=True)
class ConvertedRow:
    source_index: int
    """원본 행 번호. 좌우 패널을 잇는 앵커이자 이슈 보고의 기준."""

    kind: RowKind
    cells: dict[str, CellResult] = field(default_factory=dict)
    included: bool = True
    drop_reason: str | None = None
    """출력에서 빠진 이유. 화면의 빗금 gap 행에 그대로 표시한다."""

    @property
    def record(self) -> dict[str, Any]:
        return {key: cell.value for key, cell in self.cells.items()}


@dataclass(slots=True)
class ConversionResult:
    profile: Profile
    rows: list[ConvertedRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    """프로파일에서 원본 열을 찾지 못한 필드들."""

    source_rows: int = 0
    """원본의 데이터 행 수 (샘플링 전)."""

    @property
    def records(self) -> list[dict[str, Any]]:
        return [r.record for r in self.rows if r.included]

    @property
    def issues(self) -> list[tuple[int, str, Issue]]:
        """(원본 행 번호, 필드명, 이슈). 심각도 높은 순으로 정렬한다.

        출력에서 제외된 행은 세지 않는다. 소계 행의 ``소계`` 라는 글자를 날짜로 읽지
        못한 것은 검수 대상이 아니라 그 행을 제외한 이유이며, 이걸 오류로 올리면
        정상 파일이 ``--fail-on-error`` 에 걸린다. 값 자체는 남겨 두므로
        ``drop_subtotals`` 를 끄면 다시 이슈로 올라온다.
        """
        order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
        out = [
            (row.source_index, key, issue)
            for row in self.rows
            if row.included
            for key, cell in row.cells.items()
            for issue in cell.issues
        ]
        return sorted(out, key=lambda t: (order[t[2].severity], t[0]))

    @property
    def error_count(self) -> int:
        return sum(1 for _, _, i in self.issues if i.severity is Severity.ERROR)

    def counts(self) -> dict[str, int]:
        """진단용 셀 상태 집계. 화면 상단의 요약 숫자가 된다."""
        tally = dict.fromkeys((d.value for d in DiffKind), 0)
        for row in self.rows:
            if not row.included:
                continue
            for cell in row.cells.values():
                tally[cell.diff.value] += 1
        return tally


def convert_table(table: TableIR, profile: Profile) -> ConversionResult:
    """표 하나를 프로파일에 따라 변환한다."""
    result = ConversionResult(profile=profile, notes=list(table.notes))

    # 필드 → 원본 열 위치. 한 번만 풀어두고 행마다 재사용한다.
    column_of: dict[str, int | None] = {}
    for spec in profile.fields:
        idx = table.column_index(spec.source)
        column_of[spec.key] = idx
        if idx is None:
            result.unmapped.append(spec.key)

    if result.unmapped:
        result.notes.append(
            "원본에서 열을 찾지 못한 필드: " + ", ".join(result.unmapped) + " — 값이 비어 나갑니다"
        )

    for row in table.rows:
        if row.kind in (RowKind.HEADER, RowKind.TITLE, RowKind.BLANK):
            continue

        included = True
        reason: str | None = None
        if row.kind is RowKind.SUBTOTAL and profile.drop_subtotals:
            included = False
            reason = "소계·합계 행 — 데이터에서 제외"

        converted = ConvertedRow(
            source_index=row.index, kind=row.kind, included=included, drop_reason=reason
        )
        for spec in profile.fields:
            idx = column_of[spec.key]
            raw = None if idx is None else row.cell(idx)
            converted.cells[spec.key] = convert_cell(raw, spec)

        result.rows.append(converted)

    result.source_rows = sum(1 for r in result.rows if r.kind is RowKind.DATA)
    return result


def json_ready(value: Any) -> Any:
    """Decimal 을 JSON 이 다룰 수 있는 형태로.

    금액은 계산 중에는 Decimal 로 두고 직렬화 직전에만 변환한다. 정수로 떨어지면
    int, 아니면 float 대신 문자열로 내보내 유효자릿수를 잃지 않는다.
    """
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return format(value, "f")
    return value
