"""미리보기 대시보드용 HTTP API.

여기는 얇은 껍데기다. 실제 동작은 ``everyfile.session.Session`` 에 있고, 브라우저에서
도는 Pyodide 워커도 같은 클래스를 쓴다. 두 실행 환경이 서로 다른 코드를 타면
"브라우저에선 되는데 서버에선 값이 다르다" 가 생긴다.

화면은 프로파일을 통째로 들고 있다가 매 요청에 실어 보낸다. 서버가 세션 상태로
프로파일을 들고 있으면 여러 탭에서 같은 파일을 다른 규칙으로 보는 순간 서로를
덮어쓰기 때문이다.

주의: 저장소는 임시 디렉터리이므로 재시작하면 사라지고, 여러 워커로 띄우면 공유되지
않는다. 사내 단일 인스턴스 용도의 MVP다.
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import samples
from .profile import Profile
from .readers import SUPPORTED as READ_FORMATS
from .session import Session, SessionError
from .writers import SUPPORTED as WRITE_FORMATS

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"

STATUS = {
    "unsupported_format": 400,
    "bad_profile": 400,
    "too_large": 413,
    "unreadable": 422,
    "not_found": 404,
    "invalid": 422,
}

app = FastAPI(title="everyfile 변환 미리보기", version="0.1.0")

_storage = Path(tempfile.mkdtemp(prefix="everyfile-"))
_session = Session(storage=_storage / "files")
_profiles_dir = _storage / "profiles"
_profiles_dir.mkdir(parents=True, exist_ok=True)


def _handle(exc: SessionError) -> HTTPException:
    return HTTPException(STATUS.get(exc.code, 422), str(exc))


class PreviewRequest(BaseModel):
    file_id: str = Field(alias="fileId")
    profile: dict | None = None
    sheet: str | None = None

    model_config = {"populate_by_name": True}


class ConvertRequest(PreviewRequest):
    format: str = "json"
    encoding: str = "utf-8-sig"


class SaveProfileRequest(BaseModel):
    profile: dict


# ---------------------------------------------------------------------------
# 열기
# ---------------------------------------------------------------------------


@app.post("/api/files")
async def upload(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    try:
        return _session.open(file.filename or "upload", data)
    except SessionError as exc:
        raise _handle(exc) from exc


@app.post("/api/sample")
def load_sample() -> dict:
    """예시 장부를 등록한다. 첫 화면이 빈 상태로 열리지 않게 하는 용도."""
    try:
        return _session.open(samples.SAMPLE_NAME, samples.sample_bytes())
    except SessionError as exc:
        raise _handle(exc) from exc


# ---------------------------------------------------------------------------
# 미리보기 / 변환
# ---------------------------------------------------------------------------


@app.post("/api/preview")
def preview(req: PreviewRequest = Body(...)) -> dict:
    try:
        return _session.preview(req.file_id, req.profile, req.sheet)
    except SessionError as exc:
        raise _handle(exc) from exc


@app.post("/api/convert")
def convert(req: ConvertRequest = Body(...)) -> StreamingResponse:
    kwargs = {"encoding": req.encoding} if req.format.lower() in ("csv", "tsv") else {}
    try:
        data, filename, summary = _session.convert(
            req.file_id, req.profile, req.sheet, req.format, **kwargs
        )
    except SessionError as exc:
        raise _handle(exc) from exc

    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": _disposition(filename),
            # 화면이 다운로드 후에도 검수 상태를 표시할 수 있도록 요약을 헤더로 보낸다.
            "X-Everyfile-Rows": str(summary["rows"]),
            "X-Everyfile-Issues": str(summary["issues"]),
            "X-Everyfile-Errors": str(summary["errors"]),
            "Access-Control-Expose-Headers": "Content-Disposition, X-Everyfile-Rows,"
            " X-Everyfile-Issues, X-Everyfile-Errors",
        },
    )


def _disposition(filename: str) -> str:
    """한글 파일명은 RFC 5987 로 인코딩해 보낸다."""
    from urllib.parse import quote

    return f"attachment; filename*=utf-8''{quote(filename)}"


@app.post("/api/json-schema")
def json_schema(req: PreviewRequest = Body(...)) -> dict:
    try:
        return _session.json_schema(req.file_id, req.profile, req.sheet)
    except SessionError as exc:
        raise _handle(exc) from exc


# ---------------------------------------------------------------------------
# 프로파일 저장소
# ---------------------------------------------------------------------------


@app.get("/api/profiles")
def list_profiles() -> list[dict]:
    out = []
    for path in sorted(_profiles_dir.glob("*.json")):
        try:
            profile = Profile.load(path)
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        out.append(
            {
                "id": path.stem,
                "name": profile.name,
                "version": profile.version,
                "fields": len(profile.fields),
            }
        )
    return out


@app.post("/api/profiles")
def save_profile(req: SaveProfileRequest = Body(...)) -> dict:
    try:
        profile = Profile.from_dict(req.profile)
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(400, f"프로파일이 올바르지 않습니다: {exc}") from exc

    profile_id = uuid.uuid4().hex[:12]
    profile.save(_profiles_dir / f"{profile_id}.json")
    return {"id": profile_id, "name": profile.name, "version": profile.version}


@app.get("/api/profiles/{profile_id}")
def get_profile(profile_id: str) -> dict:
    # 경로 이탈 방지: 저장할 때 만든 형태(16진수)만 허용한다.
    if not profile_id.isalnum():
        raise HTTPException(400, "잘못된 프로파일 id 입니다")
    path = _profiles_dir / f"{profile_id}.json"
    if not path.exists():
        raise HTTPException(404, "프로파일을 찾을 수 없습니다")
    return Profile.load(path).to_dict()


# ---------------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    """화면이 실행 방식을 고르는 데 쓴다 — 이 응답이 있으면 서버 실행."""
    return {
        "status": "ok",
        "engine": "server",
        "read": list(READ_FORMATS),
        "write": list(WRITE_FORMATS),
        "open": len(_session.files),
    }


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    page = WEB_ROOT / "index.html"
    if not page.exists():  # pragma: no cover
        raise HTTPException(500, f"화면 파일을 찾을 수 없습니다: {page}")
    return HTMLResponse(page.read_text(encoding="utf-8"))


if WEB_ROOT.exists():  # pragma: no cover - 배포 형태에 따라 없을 수 있다
    app.mount("/", StaticFiles(directory=WEB_ROOT), name="web")


def cleanup() -> None:
    """임시 저장소를 지운다. 테스트와 종료 훅에서 쓴다."""
    shutil.rmtree(_storage, ignore_errors=True)
