"""엑셀(.xlsx/.xlsm) → IR."""

from __future__ import annotations

from pathlib import Path

import openpyxl

from ..detect import classify_row, detect_unit, find_header
from ..ir import Document, RowKind, SourceRef, SourceRow, TableIR


def read_xlsx(path: str | Path, *, max_rows: int | None = None) -> Document:
    """모든 시트를 표 하나씩으로 읽는다.

    ``data_only=True`` 로 수식 대신 마지막 계산값을 가져온다. 수식 문자열을 그대로
    내보내면 받는 쪽에서 쓸 수 없기 때문이다. 엑셀이 아닌 도구가 만든 파일은
    캐시된 값이 없어 None 이 나올 수 있어, 그 경우를 이슈로 남긴다.
    """
    path = Path(path)
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    doc = Document(origin=path.name, source_format=path.suffix.lstrip(".").lower())

    try:
        for ws in wb.worksheets:
            raw_rows = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if max_rows is not None and i >= max_rows:
                    break
                raw_rows.append(list(row))

            table = _build_table(ws.title, raw_rows, path.name)
            doc.tables.append(table)
    finally:
        wb.close()

    if not doc.tables:
        raise ValueError(f"{path.name}: 시트가 없습니다")
    return doc


def _build_table(sheet: str, raw_rows: list[list], origin: str) -> TableIR:
    table = TableIR(name=sheet, origin=origin)

    header_idx, header = find_header(raw_rows)
    table.header = header
    table.header_row = None if header_idx is None else header_idx + 1

    formula_cells = 0
    for idx, cells in enumerate(raw_rows):
        row_no = idx + 1

        if header_idx is not None and idx == header_idx:
            kind = RowKind.HEADER
        elif header_idx is not None and idx < header_idx:
            kind = RowKind.BLANK if not any(_filled(c) for c in cells) else RowKind.TITLE
        else:
            kind = classify_row(cells, header_seen=header_idx is not None)

        if kind is RowKind.TITLE:
            text = " ".join(str(c) for c in cells if _filled(c))
            if unit := detect_unit(text):
                table.notes.append(
                    f"{row_no}행에 단위 표기 '{unit}' 이 있습니다 — 금액은 자동 환산하지 않습니다"
                )

        formula_cells += sum(1 for c in cells if isinstance(c, str) and c.startswith("="))

        table.rows.append(
            SourceRow(
                index=row_no,
                kind=kind,
                cells=list(cells),
                ref=SourceRef(sheet=sheet, row=row_no),
            )
        )

    if formula_cells:
        table.notes.append(
            f"수식 {formula_cells}개의 계산값이 저장되어 있지 않습니다 "
            "— 엑셀에서 한 번 열어 저장한 뒤 다시 시도하세요"
        )
    if header_idx is None:
        table.notes.append("헤더 행을 찾지 못했습니다 — 컬럼 매핑을 직접 지정해야 합니다")
    elif header_idx > 0:
        table.notes.append(f"헤더를 {header_idx + 1}행에서 찾았습니다 (위 {header_idx}행은 머리글)")

    subtotals = sum(1 for r in table.rows if r.kind is RowKind.SUBTOTAL)
    if subtotals:
        table.notes.append(f"소계·합계로 보이는 {subtotals}개 행을 데이터에서 제외했습니다")

    return table


def _filled(cell: object) -> bool:
    return cell is not None and str(cell).strip() != ""
