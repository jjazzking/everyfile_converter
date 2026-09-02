"""CSV/TSV → IR.

인코딩과 구분자를 추론하는 것이 이 리더의 대부분이다. 국내 회계 자료의 CSV 는
ERP 가 CP949 로 내보낸 것과 최신 도구가 UTF-8 로 내보낸 것이 섞여 들어온다.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from ..detect import classify_row, decode_bytes, detect_unit, find_header
from ..ir import Document, RowKind, SourceRef, SourceRow, TableIR


def read_csv(path: str | Path, *, delimiter: str | None = None) -> Document:
    path = Path(path)
    text, encoding = decode_bytes(path.read_bytes())

    if delimiter is None:
        delimiter = _sniff_delimiter(text, path.suffix.lower())

    raw_rows = [list(r) for r in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)]

    table = TableIR(name=path.stem, origin=path.name, encoding=encoding)
    header_idx, header = find_header(raw_rows)
    table.header = header
    table.header_row = None if header_idx is None else header_idx + 1

    for idx, cells in enumerate(raw_rows):
        row_no = idx + 1
        if header_idx is not None and idx == header_idx:
            kind = RowKind.HEADER
        elif header_idx is not None and idx < header_idx:
            kind = RowKind.BLANK if not any(c.strip() for c in cells) else RowKind.TITLE
        else:
            kind = classify_row(cells, header_seen=header_idx is not None)

        if kind is RowKind.TITLE:
            if unit := detect_unit(" ".join(cells)):
                table.notes.append(
                    f"{row_no}행에 단위 표기 '{unit}' 이 있습니다 — 금액은 자동 환산하지 않습니다"
                )

        table.rows.append(
            SourceRow(index=row_no, kind=kind, cells=cells, ref=SourceRef(row=row_no))
        )

    table.notes.append(f"인코딩 {encoding} 로 읽었습니다")
    if encoding == "utf-8/replace":
        table.notes.append(
            "일부 문자를 해독하지 못해 대체문자로 바꿨습니다 — 인코딩을 직접 지정하세요"
        )
    if delimiter != ",":
        table.notes.append(f"구분자 {delimiter!r} 를 사용했습니다")

    doc = Document(origin=path.name, source_format=path.suffix.lstrip(".").lower())
    doc.tables.append(table)
    return doc


def _sniff_delimiter(text: str, suffix: str) -> str:
    """구분자 추정. csv.Sniffer 는 한글이 섞인 짧은 파일에서 자주 틀려 직접 센다."""
    if suffix == ".tsv":
        return "\t"

    sample = "\n".join(text.splitlines()[:20])
    if not sample:
        return ","

    best, best_score = ",", -1.0
    for cand in (",", "\t", ";", "|"):
        counts = [line.count(cand) for line in sample.splitlines() if line.strip()]
        if not counts or max(counts) == 0:
            continue
        # 모든 줄에서 같은 개수로 나타나는 후보가 진짜 구분자다.
        consistency = counts.count(max(set(counts), key=counts.count)) / len(counts)
        score = consistency * max(counts)
        if score > best_score:
            best, best_score = cand, score
    return best
