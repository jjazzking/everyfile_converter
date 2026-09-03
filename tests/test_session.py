"""세션 — 서버와 브라우저 워커가 공유하는 계층.

여기서 검증한 동작이 두 실행 환경 모두의 동작이다.
"""

from __future__ import annotations

import json

import pytest

from everyfile.samples import SAMPLE_NAME, sample_bytes
from everyfile.session import (
    BadProfile,
    NotFound,
    Session,
    TooLarge,
    Unreadable,
    UnsupportedFormat,
)


@pytest.fixture
def session(tmp_path):
    return Session(storage=tmp_path / "files")


@pytest.fixture
def opened(session):
    return session.open(SAMPLE_NAME, sample_bytes())


def test_open_returns_everything_the_first_paint_needs(opened):
    """첫 화면이 한 번의 호출로 그려져야 한다 — 나눠 보내면 빈 화면이 깜빡인다."""
    assert opened["fileId"]
    assert opened["format"] == "xlsx"
    assert opened["sheets"][0]["headerRow"] == 3
    assert [f["key"] for f in opened["profile"]["fields"]][:2] == ["entryDate", "accountCode"]
    assert opened["preview"]["output"]["rows"]


def test_open_does_not_trust_the_given_filename(session):
    """받은 이름을 경로에 쓰면 경로 이탈에 노출된다."""
    out = session.open("../../etc/passwd.csv", b"a,b\n1,2\n")
    assert out["origin"] == "passwd.csv"
    stored = session.files[out["fileId"]].path
    assert stored.parent == session.storage


def test_open_rejects_unsupported_format(session):
    with pytest.raises(UnsupportedFormat, match="지원하지 않는 형식"):
        session.open("보고서.hwp", b"x")


def test_open_rejects_unreadable_file(session):
    with pytest.raises(Unreadable, match="읽지 못했습니다"):
        session.open("깨진.xlsx", b"not-a-zip")


def test_open_rejects_oversized_file(session, monkeypatch):
    monkeypatch.setattr("everyfile.session.MAX_UPLOAD_BYTES", 10)
    with pytest.raises(TooLarge, match="너무 큽니다"):
        session.open("큰.csv", b"a,b\n" * 100)


def test_unreadable_file_is_not_left_on_disk(session):
    with pytest.raises(Unreadable):
        session.open("깨진.xlsx", b"not-a-zip")
    assert list(session.storage.glob("*")) == []


def test_preview_recomputes_when_the_profile_changes(session, opened):
    """타입 배지를 누르면 밟는 경로."""
    profile = opened["profile"]
    for field in profile["fields"]:
        if field["key"] == "accountCode":
            field["type"] = "integer"

    payload = session.preview(opened["fileId"], profile)
    idx = [c["key"] for c in payload["output"]["columns"]].index("accountCode")
    first = next(r for r in payload["output"]["rows"] if r["included"])

    assert first["cells"][idx]["display"] == "110"
    assert first["cells"][idx]["diff"] == "issue"
    assert any(i["code"] == "LEADING_ZERO_LOST" for i in payload["issues"])


def test_preview_keeps_dropped_rows_as_gaps(session, opened):
    payload = session.preview(opened["fileId"])
    gap = next(r for r in payload["output"]["rows"] if not r["included"])
    assert gap["sourceRow"] == 12
    assert "소계" in gap["dropReason"]


def test_unknown_file_is_not_found(session):
    with pytest.raises(NotFound):
        session.preview("0" * 32)


def test_malformed_profile_is_rejected(session, opened):
    with pytest.raises(BadProfile):
        session.preview(opened["fileId"], {"name": "x", "fields": [{"key": "a"}]})


def test_future_schema_version_is_rejected(session, opened):
    profile = dict(opened["profile"], schemaVersion=999)
    with pytest.raises(BadProfile, match="최신"):
        session.preview(opened["fileId"], profile)


@pytest.mark.parametrize("fmt", ["json", "xlsx", "csv"])
def test_convert_produces_every_supported_format(session, opened, fmt):
    data, filename, summary = session.convert(opened["fileId"], fmt=fmt)
    assert data
    assert filename == f"일반전표_2026Q1_예시.{fmt}"
    assert summary == {"rows": 11, "issues": 0, "errors": 0}


def test_convert_returns_the_full_file_not_the_sample(session, opened):
    """미리보기는 표본이지만 내려받기는 전체다."""
    data, _, _ = session.convert(opened["fileId"], fmt="json")
    records = json.loads(data)
    assert len(records) == 11
    assert records[0]["accountCode"] == "0110"


def test_convert_rejects_an_unsupported_format(session, opened):
    with pytest.raises(UnsupportedFormat):
        session.convert(opened["fileId"], fmt="pdf")


def test_convert_leaves_no_temporary_file_behind(session, opened):
    before = set(session.storage.glob("*"))
    session.convert(opened["fileId"], fmt="xlsx")
    assert set(session.storage.glob("*")) == before


def test_csv_output_carries_the_bom_for_excel(session, opened):
    data, _, _ = session.convert(opened["fileId"], fmt="csv", encoding="utf-8-sig")
    assert data.startswith(b"\xef\xbb\xbf")


def test_json_schema(session, opened):
    schema = session.json_schema(opened["fileId"])
    assert schema["type"] == "array"
    assert "accountCode" in schema["items"]["properties"]


def test_close_removes_the_file(session, opened):
    session.close(opened["fileId"])
    assert list(session.storage.glob("*")) == []
    with pytest.raises(NotFound):
        session.preview(opened["fileId"])
