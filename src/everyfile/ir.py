"""공통 중간표현 (Intermediate Representation).

모든 리더는 원본 포맷을 이 구조로 옮기고, 모든 라이터는 이 구조만 읽는다.
포맷이 N개여도 파서 N개 + 라이터 N개로 끝나며 N×M 조합을 만들지 않는다.

핵심 불변조건: 모든 행은 ``SourceRow.index`` 로 **원본 위치를 끝까지 유지한다**.
변환 과정에서 행이 사라지거나(소계 제외) 늘어나도(배열 평탄화) 미리보기 화면이
좌우 패널을 다시 이어붙일 수 있어야 하기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RowKind(StrEnum):
    """표 안에서 행이 맡은 역할.

    회계 실무 파일은 첫 행부터 데이터가 시작되는 경우가 거의 없다.
    제목, 단위 표기, 빈 줄, 소계가 데이터 행과 섞여 있으므로 읽는 시점에 분류해 둔다.
    """

    DATA = "data"
    """실제 데이터 행. 변환 대상."""

    HEADER = "header"
    """컬럼명이 들어있는 행."""

    TITLE = "title"
    """제목 / 단위 표기 등 표 위쪽의 머리글."""

    SUBTOTAL = "subtotal"
    """소계·합계 행. 데이터로 내보내면 이중 계상되므로 기본 제외."""

    BLANK = "blank"
    """전부 빈 행."""


@dataclass(frozen=True, slots=True)
class SourceRef:
    """셀 하나가 원본 어디에서 왔는지.

    엑셀은 (sheet, row, col) 로 충분하고, PDF 추출을 붙일 때 page/bbox 가 채워진다.
    미리보기 화면의 "원본 위치" 표시와 PDF 좌표 하이라이트가 이 값을 그대로 쓴다.
    """

    sheet: str | None = None
    row: int | None = None
    col: int | None = None
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    confidence: float | None = None
    """추출 신뢰도 (0.0~1.0). 결정적 파서(엑셀 등)는 None."""


@dataclass(slots=True)
class SourceRow:
    """원본 표의 한 행."""

    index: int
    """원본 파일 기준 1-based 행 번호. 이 값이 좌우 패널을 잇는 앵커다."""

    kind: RowKind
    cells: list[Any]
    ref: SourceRef | None = None

    def cell(self, col: int) -> Any:
        """범위를 벗어나면 None. 원본 행마다 열 개수가 다른 경우가 흔하다."""
        if 0 <= col < len(self.cells):
            return self.cells[col]
        return None


@dataclass(slots=True)
class TableIR:
    """표 하나. 엑셀 시트 하나, CSV 파일 하나, PDF 안의 표 하나에 대응한다."""

    name: str
    """시트명 또는 표 식별자."""

    header: list[str] = field(default_factory=list)
    """감지된 컬럼명. 헤더 행을 못 찾으면 빈 리스트."""

    header_row: int | None = None
    """헤더가 발견된 원본 행 번호 (1-based)."""

    rows: list[SourceRow] = field(default_factory=list)
    origin: str = ""
    """원본 파일명. 감사 로그와 화면 표시에 쓴다."""

    encoding: str | None = None
    """텍스트 원본을 디코딩한 인코딩. CSV 라운드트립에 필요하다."""

    notes: list[str] = field(default_factory=list)
    """읽는 중 발견한 사실 (단위 표기, 건너뛴 행 등). 사용자에게 그대로 보여준다."""

    @property
    def data_rows(self) -> list[SourceRow]:
        return [r for r in self.rows if r.kind is RowKind.DATA]

    @property
    def total_data_rows(self) -> int:
        return len(self.data_rows)

    def column_index(self, name: str) -> int | None:
        """헤더 이름으로 열 위치를 찾는다. 공백/대소문자 차이는 무시."""
        target = _normalize(name)
        for i, h in enumerate(self.header):
            if _normalize(h) == target:
                return i
        return None


@dataclass(slots=True)
class Document:
    """파일 하나에서 읽어낸 전체 내용.

    표 기반 포맷(엑셀·CSV·JSON)은 ``tables`` 만 채우고, 문서 기반 포맷(MD·Word)을
    붙일 때 블록 목록이 여기에 추가된다. 두 표현을 하나로 합치지 않는 이유는
    표 IR과 문서 IR이 요구하는 연산이 전혀 다르기 때문이다.
    """

    tables: list[TableIR] = field(default_factory=list)
    origin: str = ""
    source_format: str = ""

    @property
    def primary(self) -> TableIR:
        if not self.tables:
            raise ValueError(f"{self.origin!r}: 읽어들인 표가 없습니다")
        return self.tables[0]


def _normalize(s: str) -> str:
    return "".join(str(s).split()).lower()
