"""리더 — 헤더 감지, 행 분류, 인코딩."""

from __future__ import annotations

import json

from everyfile.detect import decode_bytes, detect_unit, infer_type
from everyfile.fields import FieldType
from everyfile.ir import RowKind
from everyfile.readers import read


def test_xlsx_finds_header_below_title_rows(ledger_xlsx):
    """실무 파일은 A1 부터 시작하지 않는다."""
    table = read(ledger_xlsx).primary
    assert table.header_row == 3
    assert table.header[:3] == ["전표일자", "계정코드", "계정과목"]


def test_xlsx_classifies_rows(ledger_xlsx):
    table = read(ledger_xlsx).primary
    kinds = [r.kind for r in table.rows]

    assert kinds[0] is RowKind.TITLE  # 제목
    assert kinds[1] is RowKind.TITLE  # (단위: 원)
    assert kinds[2] is RowKind.HEADER
    assert kinds[3] is RowKind.DATA
    assert RowKind.SUBTOTAL in kinds
    assert table.total_data_rows == 11  # 12행 중 소계 1행 제외


def test_source_row_anchor_is_the_original_line_number(ledger_xlsx):
    """행 앵커가 깨지면 좌우 미리보기를 이어붙일 수 없다."""
    table = read(ledger_xlsx).primary
    subtotal = next(r for r in table.rows if r.kind is RowKind.SUBTOTAL)
    assert subtotal.index == 12
    assert subtotal.ref.row == 12
    assert subtotal.ref.sheet == "일반전표"


def test_xlsx_reports_unit_note(ledger_xlsx):
    table = read(ledger_xlsx).primary
    assert any("단위 표기 '원'" in n for n in table.notes)
    assert any("자동 환산하지 않습니다" in n for n in table.notes)


def test_xlsx_reports_dropped_subtotals(ledger_xlsx):
    table = read(ledger_xlsx).primary
    assert any("소계·합계로 보이는 1개 행" in n for n in table.notes)


def test_csv_reads_cp949(ledger_csv_cp949):
    """국내 ERP 추출물은 CP949 로 온다."""
    table = read(ledger_csv_cp949).primary
    assert table.encoding == "cp949"
    assert table.header[0] == "전표일자"
    assert table.data_rows[0].cells[2] == "현금"


def test_decode_prefers_utf8_over_cp949():
    """utf-8 로 읽히는 바이트열을 cp949 로 해석하면 한글이 깨진다."""
    text, enc = decode_bytes("계정과목".encode())
    assert (text, enc) == ("계정과목", "utf-8")


def test_decode_falls_back_without_losing_data():
    text, enc = decode_bytes(b"\xff\xfe\x00bad")
    assert enc == "utf-8/replace"
    assert "bad" in text


def test_csv_detects_semicolon_delimiter(tmp_path):
    path = tmp_path / "semi.csv"
    path.write_text(
        "계정코드;계정과목;금액\n0110;현금;1,000\n0103;보통예금;2,000\n", encoding="utf-8"
    )
    table = read(path).primary
    assert table.header == ["계정코드", "계정과목", "금액"]
    assert table.data_rows[0].cells[0] == "0110"


def test_json_flattens_nested_objects(tmp_path):
    path = tmp_path / "entries.json"
    path.write_text(
        json.dumps(
            [
                {"code": "0110", "party": {"name": "대한물산", "biz": "123-45-67890"}},
                {"code": "0103", "party": {"name": "정우테크", "biz": "111-22-33333"}},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    table = read(path).primary
    assert table.header == ["code", "party.name", "party.biz"]
    assert table.data_rows[0].cells[1] == "대한물산"


def test_json_keeps_arrays_as_text(tmp_path):
    """배열을 행으로 펼치는 것은 사용자가 지정할 동작이지 기본값이 아니다."""
    path = tmp_path / "arr.json"
    path.write_text(json.dumps([{"id": 1, "tags": ["a", "b"]}]), encoding="utf-8")
    table = read(path).primary
    assert table.data_rows[0].cells[1] == '["a", "b"]'


def test_json_finds_wrapped_record_array(tmp_path):
    path = tmp_path / "wrapped.json"
    path.write_text(json.dumps({"status": "ok", "rows": [{"a": 1}, {"a": 2}]}), encoding="utf-8")
    table = read(path).primary
    assert table.total_data_rows == 2
    assert any("'rows' 키" in n for n in table.notes)


def test_detect_unit():
    assert detect_unit("(단위: 천원)") == "천원"
    assert detect_unit("단위 : 백만원") == "백만원"
    assert detect_unit("2026년 1분기") is None


def test_infer_type_prefers_code_over_number():
    """애매하면 문자열 쪽으로 — 선행 0 은 잃으면 복구할 수 없다."""
    assert infer_type(["0110", "0251", "0813"], "계정코드") is FieldType.CODE
    assert infer_type(["110", "251"], "계정코드") is FieldType.CODE
    assert infer_type(["0110", "1234"], "임의") is FieldType.CODE


def test_infer_type_detects_money_and_dates():
    assert infer_type(["1,000", "(2,000)"], "차변") is FieldType.MONEY
    assert infer_type(["20260105", "2026-01-08"], "전표일자") is FieldType.DATE
    assert infer_type(["현금", "보통예금"], "계정과목") is FieldType.TEXT


def test_decode_reports_bom_only_when_present():
    """BOM 없는 파일을 utf-8-sig 로 보고하면 다시 쓸 때 없던 BOM 이 붙는다."""
    assert decode_bytes("계정과목".encode("utf-8-sig"))[1] == "utf-8-sig"
    assert decode_bytes("계정과목".encode())[1] == "utf-8"
