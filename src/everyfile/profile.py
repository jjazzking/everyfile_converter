"""변환 프로파일 — 컬럼 매핑과 필드 타입을 담은 저장·재사용 단위.

미리보기 화면에서 헤더의 타입 배지를 눌러 바꾸는 값이 곧 이 프로파일이다.
한 번 만든 프로파일을 고객사·문서유형별로 저장해 두는 것이 제품의 핵심 가치라
JSON 으로 직렬화되고 버전을 갖는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .detect import infer_type
from .fields import FieldSpec, FieldType
from .ir import RowKind, TableIR

SCHEMA_VERSION = 1

# 원본 헤더 → 표준 필드명. 실무에서 같은 뜻으로 쓰이는 표기들을 모아둔다.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "entryDate": ("전표일자", "일자", "거래일자", "날짜", "회계일자", "date", "전표일"),
    "voucherNo": ("전표번호", "전표no", "번호", "voucherno", "docno"),
    "accountCode": ("계정코드", "계정과목코드", "계정cd", "accountcode", "acctcd", "코드"),
    "accountName": ("계정과목", "계정명", "계정", "accountname", "acctnm"),
    "description": ("적요", "내용", "비고", "description", "remark", "memo"),
    "counterparty": ("거래처", "거래처명", "상대처", "counterparty", "vendor", "customer"),
    "debit": ("차변", "차변금액", "debit", "dr"),
    "credit": ("대변", "대변금액", "credit", "cr"),
    "amount": ("금액", "거래금액", "amount"),
    "balance": ("잔액", "balance"),
}

_NON_KEY = re.compile(r"[^0-9A-Za-z가-힣]+")


@dataclass(slots=True)
class Profile:
    """필드 정의 묶음."""

    name: str
    fields: list[FieldSpec] = field(default_factory=list)
    version: int = 1
    schema_version: int = SCHEMA_VERSION
    description: str = ""
    drop_subtotals: bool = True
    """소계·합계 행을 출력에서 제외할지. 끄면 ``__rowKind`` 로 표시해 함께 내보낸다."""

    def field_by_key(self, key: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.key == key), None)

    # -- 직렬화 -------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "schemaVersion": self.schema_version,
            "description": self.description,
            "dropSubtotals": self.drop_subtotals,
            "fields": [
                {
                    "key": f.key,
                    "source": f.source,
                    "type": f.type.value,
                    "format": f.format,
                    "nullable": f.nullable,
                    "nullValues": list(f.null_values),
                    "required": f.required,
                    "decimals": f.decimals,
                }
                for f in self.fields
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Profile:
        found = data.get("schemaVersion", SCHEMA_VERSION)
        if found > SCHEMA_VERSION:
            raise ValueError(
                f"프로파일 스키마 버전 {found} 은 이 버전({SCHEMA_VERSION})보다 최신입니다"
            )
        return cls(
            name=data["name"],
            version=data.get("version", 1),
            schema_version=found,
            description=data.get("description", ""),
            drop_subtotals=data.get("dropSubtotals", True),
            fields=[
                FieldSpec(
                    key=f["key"],
                    source=f["source"],
                    type=FieldType(f.get("type", "text")),
                    format=f.get("format"),
                    nullable=f.get("nullable", True),
                    null_values=tuple(f.get("nullValues", FieldSpec.null_values)),
                    required=f.get("required", False),
                    decimals=f.get("decimals"),
                )
                for f in data.get("fields", [])
            ],
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> Profile:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # -- JSON Schema --------------------------------------------------------

    def to_json_schema(self) -> dict:
        """받는 쪽이 검증에 쓸 수 있도록 표준 JSON Schema 로 내보낸다."""
        types: dict[FieldType, dict] = {
            FieldType.TEXT: {"type": "string"},
            FieldType.CODE: {"type": "string"},
            FieldType.INTEGER: {"type": "integer"},
            FieldType.DECIMAL: {"type": "number"},
            FieldType.MONEY: {"type": "number"},
            FieldType.DATE: {"type": "string", "format": "date"},
            FieldType.BOOLEAN: {"type": "boolean"},
        }
        properties = {}
        required = []
        for f in self.fields:
            schema = dict(types[f.type])
            if f.nullable:
                schema = {"anyOf": [schema, {"type": "null"}]}
            properties[f.key] = schema
            if f.required:
                required.append(f.key)

        out: dict = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": self.name,
            "type": "array",
            "items": {"type": "object", "properties": properties, "additionalProperties": False},
        }
        if required:
            out["items"]["required"] = required
        return out


def infer_profile(table: TableIR, name: str | None = None, sample: int = 200) -> Profile:
    """표를 보고 프로파일 초안을 만든다.

    스키마를 손으로 쓰게 하면 아무도 쓰지 않는다. 자동 추론으로 시작점을 주고
    화면에서 고치게 하는 것이 전제다.
    """
    profile = Profile(name=name or table.name, description=f"{table.origin} 에서 자동 추론")

    data = [r for r in table.rows if r.kind is RowKind.DATA][:sample]
    for col, header in enumerate(table.header):
        if not header.strip():
            continue
        samples = [str(r.cell(col)) for r in data if r.cell(col) is not None]
        profile.fields.append(
            FieldSpec(
                key=suggest_key(header),
                source=header,
                type=infer_type(samples, header),
            )
        )
    return profile


def suggest_key(header: str) -> str:
    """원본 헤더에 대응하는 표준 필드명을 제안한다."""
    normalized = _NON_KEY.sub("", header).lower()
    for key, aliases in SYNONYMS.items():
        if normalized == key.lower() or normalized in aliases:
            return key
    return _to_camel(header)


def _to_camel(header: str) -> str:
    parts = [p for p in _NON_KEY.split(header) if p]
    if not parts:
        return "field"
    head, *rest = parts
    return head[0].lower() + head[1:] + "".join(p[0].upper() + p[1:] for p in rest)
