"""테스트 픽스처.

예시 장부의 정의는 ``everyfile.samples`` 한 곳에만 둔다. 테스트와 대시보드의 첫 화면이
같은 표본을 쓰므로, 화면에서 확인한 동작이 곧 테스트가 검증하는 동작이다.
"""

from __future__ import annotations

import pytest

from everyfile.samples import HEADER, ROWS, build_workbook

LEDGER_ROWS = ROWS


@pytest.fixture
def ledger_xlsx(tmp_path):
    """머리글 2행 + 헤더 3행 + 데이터, 계정코드는 텍스트로 저장."""
    path = tmp_path / "일반전표_2026Q1.xlsx"
    build_workbook().save(path)
    return path


@pytest.fixture
def ledger_csv_cp949(tmp_path):
    """ERP 가 CP949 로 내보낸 CSV."""
    lines = [",".join(HEADER)]
    for row in ROWS:
        lines.append(",".join(f'"{c}"' for c in row))
    path = tmp_path / "ledger_cp949.csv"
    path.write_bytes("\r\n".join(lines).encode("cp949"))
    return path
