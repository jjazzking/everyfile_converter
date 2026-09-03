"""HTTP API — 화면이 실제로 밟는 경로."""

from __future__ import annotations

import io
import json
import re

import pytest
from fastapi.testclient import TestClient

from everyfile.api import app
from everyfile.samples import sample_bytes


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def loaded(client):
    """예시 파일을 올린 상태. 화면이 처음 열렸을 때와 같다."""
    return client.post("/api/sample").json()


def test_sample_returns_everything_the_first_paint_needs(loaded):
    """첫 화면이 요청 한 번으로 그려져야 한다 — 왕복이 늘면 빈 화면이 깜빡인다."""
    assert loaded["fileId"]
    assert loaded["format"] == "xlsx"
    assert loaded["sheets"][0]["name"] == "일반전표"
    assert loaded["sheets"][0]["headerRow"] == 3
    assert [f["key"] for f in loaded["profile"]["fields"]][:2] == ["entryDate", "accountCode"]
    assert loaded["preview"]["output"]["rows"]


def test_upload_accepts_xlsx(client):
    res = client.post(
        "/api/files",
        files={"file": ("장부.xlsx", io.BytesIO(sample_bytes()), "application/octet-stream")},
    )
    assert res.status_code == 200
    assert res.json()["origin"] == "장부.xlsx"


def test_upload_rejects_unsupported_format(client):
    res = client.post("/api/files", files={"file": ("보고서.hwp", io.BytesIO(b"x"), "text/plain")})
    assert res.status_code == 400
    assert "지원하지 않는 형식" in res.json()["detail"]


def test_upload_rejects_unreadable_file(client):
    res = client.post("/api/files", files={"file": ("깨진.xlsx", io.BytesIO(b"not-a-zip"), "x")})
    assert res.status_code == 422
    assert "읽지 못했습니다" in res.json()["detail"]


def test_upload_does_not_trust_the_client_filename(client):
    """업로드 이름을 경로에 쓰면 경로 이탈에 노출된다."""
    res = client.post(
        "/api/files",
        files={"file": ("../../etc/passwd.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
    )
    assert res.status_code == 200
    assert res.json()["origin"] == "passwd.csv"


def test_preview_recomputes_when_the_profile_changes(client, loaded):
    """타입 배지를 누르면 밟는 경로. 서버는 프로파일을 기억하지 않는다."""
    profile = loaded["profile"]
    for field in profile["fields"]:
        if field["key"] == "accountCode":
            field["type"] = "integer"

    res = client.post(
        "/api/preview", json={"fileId": loaded["fileId"], "profile": profile}
    )
    payload = res.json()

    code_index = [c["key"] for c in payload["output"]["columns"]].index("accountCode")
    first = next(r for r in payload["output"]["rows"] if r["included"])
    assert first["cells"][code_index]["display"] == "110"
    assert first["cells"][code_index]["diff"] == "issue"
    assert any(i["code"] == "LEADING_ZERO_LOST" for i in payload["issues"])


def test_preview_keeps_dropped_rows_as_gaps(client, loaded):
    payload = client.post("/api/preview", json={"fileId": loaded["fileId"]}).json()
    gap = next(r for r in payload["output"]["rows"] if not r["included"])
    assert gap["sourceRow"] == 12
    assert "소계" in gap["dropReason"]


def test_preview_rejects_an_unknown_file(client):
    res = client.post("/api/preview", json={"fileId": "0" * 32})
    assert res.status_code == 404


def test_preview_rejects_a_malformed_profile(client, loaded):
    res = client.post(
        "/api/preview",
        json={"fileId": loaded["fileId"], "profile": {"name": "x", "fields": [{"key": "a"}]}},
    )
    assert res.status_code == 400


def test_preview_rejects_a_future_schema_version(client, loaded):
    profile = dict(loaded["profile"], schemaVersion=999)
    res = client.post("/api/preview", json={"fileId": loaded["fileId"], "profile": profile})
    assert res.status_code == 400
    assert "최신" in res.json()["detail"]


@pytest.mark.parametrize("fmt", ["json", "xlsx", "csv"])
def test_convert_downloads_every_supported_format(client, loaded, fmt):
    res = client.post("/api/convert", json={"fileId": loaded["fileId"], "format": fmt})
    assert res.status_code == 200
    assert res.headers["X-Everyfile-Rows"] == "11"
    assert len(res.content) > 0


def test_convert_returns_the_original_stem_as_the_filename(client, loaded):
    """한글 파일명은 RFC 5987 로 인코딩되어 온다 — 화면이 이 형식을 읽을 수 있어야 한다."""
    from urllib.parse import unquote

    res = client.post("/api/convert", json={"fileId": loaded["fileId"], "format": "json"})
    disposition = res.headers["content-disposition"]

    encoded = re.search(r"filename\*\s*=\s*utf-8''([^;]+)", disposition, re.I)
    assert encoded, f"filename* 형식이 아닙니다: {disposition}"
    assert unquote(encoded.group(1)) == "일반전표_2026Q1_예시.json"


def test_convert_output_carries_the_full_file_not_the_sample(client, loaded):
    """미리보기는 표본이지만 내려받기는 전체다."""
    res = client.post("/api/convert", json={"fileId": loaded["fileId"], "format": "json"})
    records = json.loads(res.content)
    assert len(records) == 11
    assert records[0]["accountCode"] == "0110"


def test_convert_rejects_an_unsupported_format(client, loaded):
    res = client.post("/api/convert", json={"fileId": loaded["fileId"], "format": "pdf"})
    assert res.status_code == 400


def test_json_schema_endpoint(client, loaded):
    schema = client.post("/api/json-schema", json={"fileId": loaded["fileId"]}).json()
    assert schema["type"] == "array"
    assert "accountCode" in schema["items"]["properties"]


def test_profiles_can_be_saved_and_read_back(client, loaded):
    """프로파일 저장·재사용이 제품의 핵심 가치다."""
    saved = client.post("/api/profiles", json={"profile": loaded["profile"]}).json()
    assert saved["id"]

    listing = client.get("/api/profiles").json()
    assert any(p["id"] == saved["id"] for p in listing)

    restored = client.get(f"/api/profiles/{saved['id']}").json()
    assert [f["key"] for f in restored["fields"]] == [
        f["key"] for f in loaded["profile"]["fields"]
    ]


def test_profile_id_cannot_traverse_paths(client):
    res = client.get("/api/profiles/..%2F..%2Fetc%2Fpasswd")
    assert res.status_code in (400, 404)


def test_index_serves_the_dashboard(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "변환 미리보기" in res.text
    assert 'src="./app.js"' in res.text


def test_page_does_not_talk_to_the_api_directly(client):
    """화면이 실행 방식을 알면 안 된다 — 그래야 브라우저 실행으로 바꿔 끼울 수 있다.

    이 불변조건이 깨지면 3단계(사내 서버)에서 화면을 통째로 다시 손대야 한다.
    """
    page = client.get("/").text
    assert "fetch(" not in page
    assert "/api/" not in page


def test_static_assets_are_served(client):
    """화면이 여러 파일로 나뉘었으므로 서버도 그것들을 내줘야 한다."""
    for path in ("/app.js", "/backend.js", "/worker.js"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert "javascript" in res.headers["content-type"]


def test_health_declares_the_engine(client):
    """화면은 이 응답을 보고 서버 실행인지 브라우저 실행인지 고른다."""
    body = client.get("/api/health").json()
    assert body["engine"] == "server"


def test_health_reports_supported_formats(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert ".xlsx" in body["read"]
    assert ".json" in body["write"]
