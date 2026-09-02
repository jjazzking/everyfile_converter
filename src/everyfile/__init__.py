"""회계 실무용 파일 변환 엔진.

포맷 조합(N×M)을 만들지 않도록 모든 변환이 공통 IR 을 거친다::

    파일 ──readers──▶ Document/TableIR ──convert──▶ ConversionResult ──writers──▶ 파일
                                              └────preview────▶ 화면 페이로드
"""

from __future__ import annotations

from .convert import ConversionResult, ConvertedRow, convert_table
from .fields import CellResult, DiffKind, FieldSpec, FieldType, Issue, Severity
from .ir import Document, RowKind, SourceRef, SourceRow, TableIR
from .pipeline import Job, convert_file, load
from .preview import build_preview
from .profile import Profile, infer_profile
from .readers import read
from .writers import write

__version__ = "0.1.0"

__all__ = [
    "CellResult",
    "ConversionResult",
    "ConvertedRow",
    "DiffKind",
    "Document",
    "FieldSpec",
    "FieldType",
    "Issue",
    "Job",
    "Profile",
    "RowKind",
    "Severity",
    "SourceRef",
    "SourceRow",
    "TableIR",
    "build_preview",
    "convert_file",
    "convert_table",
    "infer_profile",
    "load",
    "read",
    "write",
]
