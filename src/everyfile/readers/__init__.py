"""원본 포맷 → 공통 IR."""

from __future__ import annotations

from pathlib import Path

from ..ir import Document
from .csv_reader import read_csv
from .json_reader import read_json
from .xlsx_reader import read_xlsx

_BY_SUFFIX = {
    ".xlsx": read_xlsx,
    ".xlsm": read_xlsx,
    ".csv": read_csv,
    ".tsv": read_csv,
    ".json": read_json,
}

SUPPORTED = tuple(sorted(_BY_SUFFIX))


def read(path: str | Path) -> Document:
    """확장자를 보고 알맞은 리더로 읽는다."""
    path = Path(path)
    reader = _BY_SUFFIX.get(path.suffix.lower())
    if reader is None:
        raise ValueError(
            f"{path.name}: 지원하지 않는 입력 형식입니다 (지원: {', '.join(SUPPORTED)})"
        )
    return reader(path)


__all__ = ["SUPPORTED", "read", "read_csv", "read_json", "read_xlsx"]
