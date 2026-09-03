"""파일 하나를 열어두고 규칙을 바꿔가며 미리보는 세션.

FastAPI 서버와 브라우저(Pyodide) 워커가 **같은 클래스**를 쓴다. 두 실행 환경이
서로 다른 코드를 타면 "브라우저에선 되는데 서버에선 값이 다르다" 가 반드시 생긴다.
프레임워크에 기대지 않도록 HTTP 개념은 여기 들어오지 않는다 — 예외만 던지고,
그것을 상태코드로 옮기는 일은 API 층이 한다.

세션은 열어둔 파일의 **파싱 결과**를 들고 있는다. 타입을 바꿀 때마다 파일을 다시
읽으면 3만 행에서 클릭 한 번에 3초씩 걸린다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ir import Document
from .pipeline import build
from .preview import build_preview
from .profile import Profile
from .readers import SUPPORTED as READ_FORMATS
from .readers import read
from .writers import SUPPORTED as WRITE_FORMATS
from .writers import write

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
"""열 수 있는 파일 크기 상한. 이보다 큰 장부는 CLI 로 배치 처리하는 편이 맞다."""


class SessionError(Exception):
    """세션 작업 실패. ``code`` 로 호출자가 상태코드를 정한다."""

    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


class UnsupportedFormat(SessionError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "unsupported_format")


class TooLarge(SessionError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "too_large")


class Unreadable(SessionError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "unreadable")


class NotFound(SessionError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "not_found")


class BadProfile(SessionError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "bad_profile")


@dataclass(slots=True)
class OpenFile:
    file_id: str
    origin: str
    path: Path
    document: Document
    suffix: str


@dataclass(slots=True)
class Session:
    """열어둔 파일들을 들고 있는 저장소.

    브라우저에서는 탭 하나가 세션 하나다. 서버에서는 프로세스 하나가 세션 하나이며,
    재시작하면 사라진다 — 영구 저장소는 3단계(사내 서버)의 몫이다.
    """

    storage: Path
    files: dict[str, OpenFile] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.storage = Path(self.storage)
        self.storage.mkdir(parents=True, exist_ok=True)

    # -- 열기 ---------------------------------------------------------------

    def open(self, name: str, data: bytes) -> dict[str, Any]:
        """파일을 등록하고 첫 화면에 필요한 것을 한 번에 돌려준다.

        프로파일 초안과 미리보기까지 함께 보내는 이유는 왕복을 줄이기 위해서다.
        나눠 보내면 화면이 빈 상태로 한 번 깜빡인다.
        """
        suffix = Path(name).suffix.lower()
        if suffix not in READ_FORMATS:
            raise UnsupportedFormat(
                f"지원하지 않는 형식입니다: {suffix or '(확장자 없음)'}"
                f" — 가능: {', '.join(READ_FORMATS)}"
            )
        if len(data) > MAX_UPLOAD_BYTES:
            raise TooLarge(
                f"파일이 너무 큽니다 ({len(data) / 1e6:.1f}MB)"
                f" — 상한 {MAX_UPLOAD_BYTES // 10**6}MB"
            )

        file_id = uuid.uuid4().hex
        # 저장 경로는 UUID 로 짓는다. 받은 이름을 경로에 쓰면 경로 이탈에 노출된다.
        path = self.storage / f"{file_id}{suffix}"
        path.write_bytes(data)

        try:
            document = read(path)
            job = build(document)
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise Unreadable(f"파일을 읽지 못했습니다: {exc}") from exc

        origin = Path(name).name
        self.files[file_id] = OpenFile(file_id, origin, path, document, suffix)

        return {
            "fileId": file_id,
            "origin": origin,
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
                for t in document.tables
            ],
            "profile": job.profile.to_dict(),
            "preview": job.preview(),
        }

    def close(self, file_id: str) -> None:
        entry = self.files.pop(file_id, None)
        if entry is not None:
            entry.path.unlink(missing_ok=True)

    # -- 미리보기 / 변환 ----------------------------------------------------

    def preview(
        self, file_id: str, profile: dict | None = None, sheet: str | None = None
    ) -> dict[str, Any]:
        """규칙을 바꿀 때마다 호출된다 — 타입 배지를 누르면 여기로 온다."""
        job = self._job(file_id, profile, sheet)
        return build_preview(job.table, job.result, job.profile)

    def convert(
        self,
        file_id: str,
        profile: dict | None = None,
        sheet: str | None = None,
        fmt: str = "json",
        **write_kwargs: Any,
    ) -> tuple[bytes, str, dict[str, int]]:
        """전체 행을 변환한다. (바이트, 파일명, 요약) 을 돌려준다.

        미리보기는 표본이지만 여기는 전부다.
        """
        suffix = "." + fmt.lstrip(".").lower()
        if suffix not in WRITE_FORMATS:
            raise UnsupportedFormat(
                f"지원하지 않는 출력 형식입니다 — 가능: {', '.join(WRITE_FORMATS)}"
            )

        entry = self._entry(file_id)
        job = self._job(file_id, profile, sheet)

        out = self.storage / f"{uuid.uuid4().hex}{suffix}"
        try:
            write(job.result, out, **write_kwargs)
            data = out.read_bytes()
        finally:
            out.unlink(missing_ok=True)

        summary = {
            "rows": len(job.result.records),
            "issues": len(job.result.issues),
            "errors": job.result.error_count,
        }
        return data, f"{Path(entry.origin).stem}{suffix}", summary

    def json_schema(
        self, file_id: str, profile: dict | None = None, sheet: str | None = None
    ) -> dict[str, Any]:
        return self._job(file_id, profile, sheet).profile.to_json_schema()

    # -- 내부 ---------------------------------------------------------------

    def _entry(self, file_id: str) -> OpenFile:
        entry = self.files.get(file_id)
        if entry is None:
            raise NotFound("열어둔 파일을 찾을 수 없습니다 — 파일을 다시 올려주세요")
        return entry

    def _job(self, file_id: str, profile: dict | None, sheet: str | None):
        entry = self._entry(file_id)

        parsed = None
        if profile is not None:
            try:
                parsed = Profile.from_dict(profile)
            except (KeyError, ValueError, TypeError) as exc:
                raise BadProfile(f"프로파일이 올바르지 않습니다: {exc}") from exc

        try:
            # 파싱 결과를 재사용한다 — 규칙만 다시 적용하면 된다.
            return build(entry.document, profile=parsed, sheet=sheet)
        except ValueError as exc:
            raise SessionError(str(exc)) from exc
