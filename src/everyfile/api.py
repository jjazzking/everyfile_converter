"""미리보기 대시보드용 HTTP API.

화면은 프로파일을 통째로 들고 있다가 매 요청에 실어 보낸다. 서버가 세션 상태로
프로파일을 들고 있으면 여러 탭에서 같은 파일을 다른 규칙으로 보는 순간 서로를
덮어쓰기 때문이다. 서버가 보관하는 것은 업로드된 원본 파일뿐이다.

주의: 업로드 저장소는 프로세스 메모리 + 임시 디렉터리이므로 재시작하면 사라지고
여러 워커로 띄우면 공유되지 않는다. 사내 단일 인스턴스 용도의 MVP다.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from . import samples
from .ir import Document
from .pipeline import build
from .profile import Profile
from .readers import SUPPORTED as READ_FORMATS
from .readers import read
from .writers import SUPPORTED as WRITE_FORMATS

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
"""업로드 상한. 이보다 큰 장부는 CLI 로 배치 처리하는 편이 맞다."""

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"

app = FastAPI(title="everyfile 변환 미리보기", version="0.1.0")

_storage = Path(tempfile.mkdtemp(prefix="everyfile-"))
_uploads: dict[str, dict[str, Any]] = {}
_profiles_dir = _storage / "profiles"
_profiles_dir.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# 요청 모델
# ---------------------------------------------------------------------------


class PreviewRequest(BaseModel):
    file_id: str = Field(alias="fileId")
    profile: dict | None = None
    sheet: str | None = None

    model_config = {"populate_by_name": True}


class ConvertRequest(PreviewRequest):
    format: str = "json"


class SaveProfileRequest(BaseModel):
    profile: dict


# ---------------------------------------------------------------------------
# 업로드
# ---------------------------------------------------------------------------


def _register(name: str, data: bytes) -> dict[str, Any]:
    """파일을 저장하고 프로파일 초안까지 만들어 돌려준다.

    저장 경로는 UUID 로 짓고 원본 파일명은 메타데이터로만 들고 있는다. 업로드된
    이름을 경로에 그대로 쓰면 경로 이탈(``../``)에 노출된다.
    """
    suffix = Path(name).suffix.lower()
    if suffix not in READ_FORMATS:
        raise HTTPException(
            400,
            f"지원하지 않는 형식입니다: {suffix or '(확장자 없음)'}"
            f" — 가능: {', '.join(READ_FORMATS)}",
        )
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413,
            f"파일이 너무 큽니다 ({len(data) / 1e6:.1f}MB)"
            f" — 상한 {MAX_UPLOAD_BYTES // 10**6}MB",
        )

    file_id = uuid.uuid4().hex
    path = _storage / f"{file_id}{suffix}"
    path.write_bytes(data)

    try:
        document = read(path)
        job = build(document)
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(422, f"파일을 읽지 못했습니다: {exc}") from exc

    _uploads[file_id] = {
        "path": path,
        "document": document,
        "origin": Path(name).name,
        "uploaded_at": datetime.now(UTC).isoformat(),
    }

    return {
        "fileId": file_id,
        "origin": Path(name).name,
        "format": suffix.lstrip("."),
        "sheets": [
            {
                "name": t.name,
                "headerRow": t.header_row,
                "header": t.header,
                "totalRows": t.total_data_rows,
                "encoding": t.encoding,
                "notes": t.notes,
            }
            for t in job.document.tables
        ],
        "profile": job.profile.to_dict(),
        "preview": job.preview(),
    }


@app.post("/api/files")
async def upload(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    return _register(file.filename or "upload", data)


@app.post("/api/sample")
def load_sample() -> dict:
    """예시 장부를 등록한다. 첫 화면이 빈 상태로 열리지 않게 하는 용도."""
    return _register(samples.SAMPLE_NAME, samples.sample_bytes())


# ---------------------------------------------------------------------------
# 미리보기 / 변환
# ---------------------------------------------------------------------------


def _job(req: PreviewRequest):
    entry = _uploads.get(req.file_id)
    if entry is None:
        raise HTTPException(404, "업로드를 찾을 수 없습니다 — 파일을 다시 올려주세요")

    profile = None
    if req.profile is not None:
        try:
            profile = Profile.from_dict(req.profile)
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, f"프로파일이 올바르지 않습니다: {exc}") from exc

    # 파싱 결과를 재사용한다 — 타입을 바꿀 때마다 파일을 다시 읽으면 클릭이 느려진다.
    document: Document = entry["document"]
    try:
        return build(document, profile=profile, sheet=req.sheet)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/preview")
def preview(req: PreviewRequest = Body(...)) -> dict:
    """프로파일을 바꿀 때마다 호출된다 — 타입 배지를 누르면 곧바로 여기로 온다."""
    return _job(req).preview()


@app.post("/api/convert")
def convert(req: ConvertRequest = Body(...)) -> FileResponse:
    suffix = "." + req.format.lstrip(".").lower()
    if suffix not in WRITE_FORMATS:
        raise HTTPException(
            400, f"지원하지 않는 출력 형식입니다 — 가능: {', '.join(WRITE_FORMATS)}"
        )

    job = _job(req)
    stem = Path(_uploads[req.file_id]["origin"]).stem
    out = _storage / f"{uuid.uuid4().hex}{suffix}"
    job.save(out)

    return FileResponse(
        out,
        filename=f"{stem}{suffix}",
        media_type="application/octet-stream",
        headers={
            # 화면이 다운로드 후에도 검수 상태를 표시할 수 있도록 요약을 헤더로 보낸다.
            "X-Everyfile-Rows": str(len(job.result.records)),
            "X-Everyfile-Issues": str(len(job.result.issues)),
            "X-Everyfile-Errors": str(job.result.error_count),
        },
    )


@app.post("/api/json-schema")
def json_schema(req: PreviewRequest = Body(...)) -> dict:
    return _job(req).profile.to_json_schema()


# ---------------------------------------------------------------------------
# 프로파일 저장소
# ---------------------------------------------------------------------------


@app.get("/api/profiles")
def list_profiles() -> list[dict]:
    out = []
    for path in sorted(_profiles_dir.glob("*.json")):
        try:
            profile = Profile.load(path)
        except (ValueError, KeyError):
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
    except (KeyError, ValueError) as exc:
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


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    page = WEB_ROOT / "index.html"
    if not page.exists():  # pragma: no cover
        raise HTTPException(500, f"화면 파일을 찾을 수 없습니다: {page}")
    return HTMLResponse(page.read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "read": list(READ_FORMATS),
        "write": list(WRITE_FORMATS),
        "uploads": len(_uploads),
    }


def cleanup() -> None:
    """임시 저장소를 지운다. 테스트와 종료 훅에서 쓴다."""
    shutil.rmtree(_storage, ignore_errors=True)
