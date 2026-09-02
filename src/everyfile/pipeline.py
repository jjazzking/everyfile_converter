"""읽기 → 변환 → 쓰기를 잇는 최상위 진입점."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .convert import ConversionResult, convert_table
from .ir import Document, TableIR
from .preview import build_preview
from .profile import Profile, infer_profile
from .readers import read
from .writers import write


@dataclass(slots=True)
class Job:
    """변환 한 건. 원본·프로파일·결과를 함께 들고 있어 감사 로그로 남기기 좋다."""

    document: Document
    table: TableIR
    profile: Profile
    result: ConversionResult

    def preview(self) -> dict:
        return build_preview(self.table, self.result, self.profile)

    def save(self, path: str | Path, **kwargs) -> Path:
        return write(self.result, path, **kwargs)


def load(
    source: str | Path,
    *,
    profile: Profile | None = None,
    sheet: str | None = None,
) -> Job:
    """파일을 읽고 프로파일을 적용한다. 프로파일이 없으면 추론해서 초안을 만든다."""
    document = read(source)

    if sheet is None:
        table = document.primary
    else:
        table = next((t for t in document.tables if t.name == sheet), None)
        if table is None:
            names = ", ".join(t.name for t in document.tables)
            raise ValueError(f"시트 {sheet!r} 를 찾을 수 없습니다 (있는 시트: {names})")

    if profile is None:
        profile = infer_profile(table)

    return Job(
        document=document,
        table=table,
        profile=profile,
        result=convert_table(table, profile),
    )


def convert_file(
    source: str | Path,
    target: str | Path,
    *,
    profile: Profile | None = None,
    sheet: str | None = None,
    **write_kwargs,
) -> Job:
    """한 줄로 변환. 결과 Job 을 돌려주므로 이슈를 이어서 확인할 수 있다."""
    job = load(source, profile=profile, sheet=sheet)
    job.save(target, **write_kwargs)
    return job
