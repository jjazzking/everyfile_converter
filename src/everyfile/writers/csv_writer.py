"""변환 결과 → CSV/TSV.

두 가지가 이 라이터의 존재 이유다: **수식 인젝션 방어**와 **인코딩 선택**.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..convert import ConversionResult

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
"""엑셀·구글시트가 수식으로 해석하는 시작 문자.

받은 CSV 를 열었을 뿐인데 명령이 실행되는 사고(CSV injection)를 막는다.
회계법인은 외부에서 받은 파일을 변환해 다시 배포하는 일이 잦아 이 방어가 필수다.
"""


def write_csv(
    result: ConversionResult,
    path: str | Path,
    *,
    encoding: str = "utf-8-sig",
    delimiter: str | None = None,
    sanitize: bool = True,
) -> Path:
    """CSV 로 내보낸다.

    기본 인코딩이 ``utf-8-sig`` 인 이유: BOM 이 없으면 한국어 윈도우 엑셀이
    UTF-8 CSV 를 CP949 로 읽어 한글이 전부 깨진다. 구형 시스템에 넣어야 하면
    ``encoding="cp949"`` 를 지정한다.
    """
    path = Path(path)
    if delimiter is None:
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","

    keys = [f.key for f in result.profile.fields]

    with path.open("w", encoding=encoding, newline="") as fh:
        writer = csv.writer(fh, delimiter=delimiter, lineterminator="\r\n")
        writer.writerow([_escape(k) if sanitize else k for k in keys])
        for row in result.rows:
            if not row.included:
                continue
            cells = [_stringify(row.cells[k].value) for k in keys]
            writer.writerow([_escape(c) for c in cells] if sanitize else cells)

    return path


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _escape(text: str) -> str:
    """수식으로 해석될 수 있는 값 앞에 작은따옴표를 붙인다.

    음수 금액(``-1240000``)까지 막지 않도록, 숫자로 온전히 읽히는 값은 통과시킨다.
    """
    if not text or not text.startswith(FORMULA_PREFIXES):
        return text
    if _is_plain_number(text):
        return text
    return "'" + text


def _is_plain_number(text: str) -> bool:
    try:
        Decimal(text)
    except Exception:
        return False
    return True
