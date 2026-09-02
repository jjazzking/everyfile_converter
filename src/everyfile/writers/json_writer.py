"""변환 결과 → JSON."""

from __future__ import annotations

import json
from pathlib import Path

from ..convert import ConversionResult, json_ready


def write_json(
    result: ConversionResult,
    path: str | Path,
    *,
    indent: int = 2,
    with_source_row: bool = False,
) -> Path:
    """객체 배열로 내보낸다.

    ``with_source_row=True`` 면 각 레코드에 ``__sourceRow`` 를 남긴다. 변환 결과를
    원본으로 되짚어야 하는 검수 단계에서 쓴다 — 기본으로 켜면 받는 시스템의
    스키마를 오염시키므로 명시적 선택으로 둔다.
    """
    path = Path(path)
    records = []
    for row in result.rows:
        if not row.included:
            continue
        record = {key: json_ready(cell.value) for key, cell in row.cells.items()}
        if with_source_row:
            record["__sourceRow"] = row.source_index
        records.append(record)

    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8"
    )
    return path
