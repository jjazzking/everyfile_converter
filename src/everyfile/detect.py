"""원본 파일을 읽을 때 필요한 추론들 — 인코딩, 헤더 위치, 행 분류.

회계 실무 파일은 A1 부터 데이터가 시작되지 않는다. 제목, 단위 표기, 빈 줄이 위에 붙고
중간에 소계가 섞인다. 이 추론을 리더마다 다시 구현하지 않도록 한 곳에 모은다.
"""

from __future__ import annotations

import codecs
import re

from .fields import FieldType, parse_date, parse_number
from .ir import RowKind

# 한국 회계 자료에서 CSV 로 마주치는 인코딩. 순서가 중요하다:
# utf-8 로 성공적으로 디코딩되는 바이트열이 cp949 로도 디코딩될 수 있으므로 utf-8 을 먼저 본다.
ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr")

_SUBTOTAL_WORDS = ("소계", "합계", "총계", "누계", "계", "小計", "合計", "subtotal", "total")
_UNIT_PATTERN = re.compile(r"\(?\s*단위\s*[:：]\s*([^)\]]+)\)?")


def decode_bytes(data: bytes) -> tuple[str, str]:
    """텍스트 바이트를 디코딩하고 사용한 인코딩을 함께 돌려준다.

    라이브러리를 쓰지 않고 후보를 순서대로 시도한다. 통계 기반 추정기는 짧은 파일에서
    한글 CP949 를 자주 틀리는데, 회계 자료의 인코딩 후보는 사실상 이 넷뿐이라
    순차 시도가 더 정확하고 결과도 재현 가능하다.

    BOM 유무를 먼저 가른다. ``utf-8-sig`` 코덱은 BOM 없는 UTF-8 도 그대로 읽어내므로
    구분 없이 시도하면 BOM 이 없던 파일까지 ``utf-8-sig`` 로 보고되고, 그대로 다시
    쓰면 없던 BOM 이 생겨 라운드트립이 깨진다.
    """
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig"), "utf-8-sig"

    for enc in ENCODINGS:
        if enc == "utf-8-sig":
            continue
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    # 어느 것도 안 되면 데이터를 버리지 않고 대체문자로 살린다 (이슈로 보고됨).
    return data.decode("utf-8", errors="replace"), "utf-8/replace"


def detect_unit(text: str) -> str | None:
    """``(단위: 천원)`` 같은 표기에서 배수 문구를 뽑는다.

    금액을 자동으로 곱하지는 않는다 — 단위 표기가 표 전체에 적용되는지 일부 열에만
    적용되는지는 파일마다 다르고, 잘못 곱하면 조용히 1000배 틀린 값이 나간다.
    사용자에게 알리는 것까지가 여기 책임이다.
    """
    m = _UNIT_PATTERN.search(text)
    return m.group(1).strip() if m else None


def classify_row(cells: list, header_seen: bool) -> RowKind:
    """행 하나의 역할을 판정한다."""
    values = [str(c).strip() for c in cells if c is not None and str(c).strip()]
    if not values:
        return RowKind.BLANK

    first = values[0]
    if any(w == first or first.startswith(w) for w in _SUBTOTAL_WORDS):
        # 첫 칸이 '소계'인데 나머지가 전부 비어있으면 그냥 라벨 행이다.
        return RowKind.SUBTOTAL if len(values) > 1 else RowKind.TITLE

    # 채워진 칸이 하나뿐이면 제목/단위 표기로 본다.
    if len(values) == 1 and not header_seen:
        return RowKind.TITLE

    return RowKind.DATA


def find_header(rows: list[list], max_scan: int = 12) -> tuple[int | None, list[str]]:
    """헤더 행의 위치(0-based)와 컬럼명을 찾는다.

    점수 기준: 채워진 칸이 많고, 값이 전부 짧은 문자열이며, 숫자·날짜로 읽히지 않는 행.
    데이터 행은 반드시 숫자나 날짜를 포함하므로 이 조건으로 갈린다.
    """
    best_score = 0.0
    best_idx: int | None = None

    for idx, row in enumerate(rows[:max_scan]):
        values = [str(c).strip() for c in row if c is not None and str(c).strip()]
        if len(values) < 2:
            continue

        texty = sum(1 for v in values if not _looks_numeric(v))
        score = texty / len(values) * len(values)

        # 바로 아래 행이 데이터처럼 보이면 헤더일 가능성이 크게 올라간다.
        if idx + 1 < len(rows):
            below = [str(c).strip() for c in rows[idx + 1] if c is not None and str(c).strip()]
            if below and any(_looks_numeric(v) for v in below):
                score *= 1.8

        if score > best_score:
            best_score, best_idx = score, idx

    if best_idx is None:
        return None, []

    header = [str(c).strip() if c is not None else "" for c in rows[best_idx]]
    while header and not header[-1]:
        header.pop()
    return best_idx, header


def infer_type(samples: list[str], name: str = "") -> FieldType:
    """열의 표본값들로 필드 타입을 추론한다.

    ``코드`` 를 ``숫자`` 보다 먼저 보는 것이 핵심이다. 선행 0 이 있는 값은
    숫자로 읽는 순간 복구할 수 없으므로, 애매하면 문자열 쪽으로 기운다.
    """
    values = [s.strip() for s in samples if s and s.strip()]
    if not values:
        return FieldType.TEXT

    lowered = name.replace(" ", "").lower()
    if any(k in lowered for k in ("코드", "code", "번호", "no", "id", "계좌")):
        if all(_looks_numeric(v) for v in values):
            return FieldType.CODE

    # 선행 0 이 하나라도 있으면 무조건 코드로 본다.
    if any(len(v) > 1 and v.startswith("0") and v[1:].isdigit() for v in values):
        return FieldType.CODE

    if all(parse_date(v)[0] is not None for v in values):
        return FieldType.DATE

    numeric = [parse_number(v)[0] for v in values]
    if all(n is not None for n in numeric):
        if any(k in lowered for k in ("금액", "차변", "대변", "잔액", "단가", "합계", "원")):
            return FieldType.MONEY
        if all(n == n.to_integral_value() for n in numeric if n is not None):
            # 자릿수가 크고 콤마가 있으면 금액일 가능성이 높다.
            if any("," in v for v in values):
                return FieldType.MONEY
            return FieldType.INTEGER
        return FieldType.DECIMAL

    return FieldType.TEXT


def _looks_numeric(value: str) -> bool:
    return parse_number(value)[0] is not None or parse_date(value)[0] is not None
