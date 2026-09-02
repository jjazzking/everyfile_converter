"""변환 결과 → 출력 포맷."""

from __future__ import annotations

from pathlib import Path

from ..convert import ConversionResult
from .csv_writer import write_csv
from .json_writer import write_json
from .xlsx_writer import write_xlsx

_BY_SUFFIX = {
    ".json": write_json,
    ".xlsx": write_xlsx,
    ".csv": write_csv,
    ".tsv": write_csv,
}

SUPPORTED = tuple(sorted(_BY_SUFFIX))


def write(result: ConversionResult, path: str | Path, **kwargs) -> Path:
    """확장자를 보고 알맞은 라이터로 쓴다."""
    path = Path(path)
    writer = _BY_SUFFIX.get(path.suffix.lower())
    if writer is None:
        raise ValueError(
            f"{path.name}: 지원하지 않는 출력 형식입니다 (지원: {', '.join(SUPPORTED)})"
        )
    return writer(result, path, **kwargs)


__all__ = ["SUPPORTED", "write", "write_csv", "write_json", "write_xlsx"]
