"""JSON → IR.

객체 배열을 표로 편다. 중첩 객체는 ``고객사.대표자`` 처럼 점으로 이은 열로 평탄화하고,
배열은 원본 정보를 잃지 않도록 JSON 문자열로 유지한다 — 행 확장(폭발)은 사용자가
명시적으로 지정해야 하는 동작이지 기본값이 될 수 없다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..detect import decode_bytes
from ..ir import Document, RowKind, SourceRef, SourceRow, TableIR

MAX_DEPTH = 4


def read_json(path: str | Path, *, root: str | None = None) -> Document:
    path = Path(path)
    text, encoding = decode_bytes(path.read_bytes())
    data = json.loads(text)

    records, note = _find_records(data, root)
    table = TableIR(name=path.stem, origin=path.name, encoding=encoding)
    if note:
        table.notes.append(note)

    columns: list[str] = []
    flattened: list[dict[str, Any]] = []
    for record in records:
        flat = _flatten(record)
        flattened.append(flat)
        for key in flat:
            if key not in columns:
                columns.append(key)

    table.header = columns
    table.header_row = 1
    table.rows.append(
        SourceRow(index=1, kind=RowKind.HEADER, cells=list(columns), ref=SourceRef(row=1))
    )

    for i, flat in enumerate(flattened):
        row_no = i + 2
        table.rows.append(
            SourceRow(
                index=row_no,
                kind=RowKind.DATA,
                cells=[flat.get(c) for c in columns],
                ref=SourceRef(row=row_no),
            )
        )

    ragged = sum(1 for f in flattened if len(f) != len(columns))
    if ragged:
        table.notes.append(f"{ragged}개 레코드의 키 구성이 달라 빈 칸으로 채웠습니다")

    doc = Document(origin=path.name, source_format="json")
    doc.tables.append(table)
    return doc


def _find_records(data: Any, root: str | None) -> tuple[list[dict], str | None]:
    """표로 펼 객체 배열을 찾는다."""
    if root is not None:
        if not isinstance(data, dict) or root not in data:
            raise ValueError(f"루트 키 {root!r} 를 찾을 수 없습니다")
        return _as_records(data[root]), f"루트 키 {root!r} 아래를 읽었습니다"

    if isinstance(data, list):
        return _as_records(data), None

    if isinstance(data, dict):
        # {"rows": [...]} / {"data": {"items": [...]}} 처럼 감싸인 형태가 흔하다.
        for key, value in data.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return _as_records(value), f"객체 배열이 있는 {key!r} 키를 표로 읽었습니다"
        return [data], "최상위 객체 하나를 한 행으로 읽었습니다"

    raise ValueError("객체 배열 또는 객체를 기대했습니다")


def _as_records(value: Any) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError("배열을 기대했습니다")
    out = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{i}번째 항목이 객체가 아닙니다 ({type(item).__name__})")
        out.append(item)
    return out


def _flatten(obj: dict, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in obj.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and depth < MAX_DEPTH:
            flat.update(_flatten(value, name, depth + 1))
        elif isinstance(value, (list, dict)):
            # 원문 유지. 사용자가 행 확장 규칙을 지정하면 그때 펼친다.
            flat[name] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            flat[name] = value
        else:
            flat[name] = value
    return flat
