"""미리보기 페이로드 — 화면이 그대로 렌더링할 수 있는 형태.

두 가지가 설계의 전부다.

1. **샘플링**: 전체를 매번 변환하면 타입을 바꿀 때마다 화면이 멈춘다. 그렇다고 앞
   100행만 보면 3만 번째 행에서 깨지는 파일을 놓친다. 그래서 *앞 N행 + 이슈 행 +
   결정적 무작위 N행* 을 섞는다.
2. **행 앵커**: 좌우 패널이 원본 행 번호로 이어지고, 빠진 행은 gap 으로 자리를 남긴다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from .convert import ConversionResult, json_ready
from .fields import Severity
from .ir import RowKind, TableIR
from .profile import Profile

HEAD_ROWS = 20
RANDOM_ROWS = 20
ISSUE_ROWS = 20
SAMPLE_SEED = 20260101
"""고정 시드. 같은 파일에 대해 미리보기가 매번 같아야 사용자가 비교할 수 있다."""


@dataclass(slots=True)
class Sampling:
    total: int
    shown: int
    head: int
    issues: int
    random: int

    def describe(self) -> str:
        parts = [f"상위 {self.head}행"]
        if self.issues:
            parts.append(f"이슈 {self.issues}행")
        if self.random:
            parts.append(f"무작위 {self.random}행")
        return " + ".join(parts) + f" · 전체 {self.total:,}행"


def select_rows(result: ConversionResult) -> tuple[list[int], Sampling]:
    """미리보기에 실을 행의 원본 번호를 고른다.

    반환 순서는 원본 순서를 유지한다 — 화면에서 원본과 나란히 놓이므로
    표본 추출 순서대로 섞어 보여주면 대조가 불가능해진다.
    """
    rows = result.rows
    total = sum(1 for r in rows if r.kind is RowKind.DATA)

    head = [r.source_index for r in rows[:HEAD_ROWS]]
    chosen = dict.fromkeys(head)

    issue_rows = [
        r.source_index
        for r in rows
        if r.source_index not in chosen
        and any(
            i.severity is not Severity.INFO for c in r.cells.values() for i in c.issues
        )
    ][:ISSUE_ROWS]
    for idx in issue_rows:
        chosen[idx] = None

    remaining = [r.source_index for r in rows if r.source_index not in chosen]
    picked: list[int] = []
    if remaining:
        rng = random.Random(SAMPLE_SEED)
        picked = rng.sample(remaining, min(RANDOM_ROWS, len(remaining)))
        for idx in picked:
            chosen[idx] = None

    order = sorted(chosen)
    sampling = Sampling(
        total=total,
        shown=sum(1 for r in rows if r.source_index in chosen and r.kind is RowKind.DATA),
        head=len(head),
        issues=len(issue_rows),
        random=len(picked),
    )
    return order, sampling


def build_preview(table: TableIR, result: ConversionResult, profile: Profile) -> dict[str, Any]:
    """화면이 소비하는 미리보기 계약.

    ``source`` 와 ``output`` 의 행은 같은 ``sourceRow`` 로 짝지어져 있고,
    출력에서 빠진 행도 ``included: false`` 로 자리를 지킨다.
    """
    indices, sampling = select_rows(result)
    wanted = set(indices)

    by_index = {r.source_index: r for r in result.rows}
    source_rows = {r.index: r for r in table.rows}

    source_payload = []
    output_payload = []

    for idx in indices:
        src = source_rows.get(idx)
        conv = by_index[idx]

        source_payload.append(
            {
                "sourceRow": idx,
                "kind": conv.kind.value,
                "cells": ["" if c is None else str(c) for c in (src.cells if src else [])],
            }
        )

        output_payload.append(
            {
                "sourceRow": idx,
                "kind": conv.kind.value,
                "included": conv.included,
                "dropReason": conv.drop_reason,
                "cells": [
                    {
                        "key": spec.key,
                        "display": conv.cells[spec.key].display,
                        "value": json_ready(conv.cells[spec.key].value),
                        "diff": conv.cells[spec.key].diff.value,
                        "rules": conv.cells[spec.key].rules,
                        "issues": [
                            {
                                "code": i.code,
                                "message": i.message,
                                "severity": i.severity.value,
                            }
                            for i in conv.cells[spec.key].issues
                        ],
                    }
                    for spec in profile.fields
                ],
            }
        )

    return {
        "origin": table.origin,
        "sheet": table.name,
        "profile": {"name": profile.name, "version": profile.version},
        "source": {
            "header": table.header,
            "headerRow": table.header_row,
            "rows": source_payload,
        },
        "output": {
            "columns": [
                {
                    "key": spec.key,
                    "source": spec.source,
                    "type": spec.type.value,
                    "format": spec.format,
                    "nullable": spec.nullable,
                    "mapped": spec.key not in result.unmapped,
                }
                for spec in profile.fields
            ],
            "rows": output_payload,
        },
        "sampling": {
            "total": sampling.total,
            "shown": sampling.shown,
            "head": sampling.head,
            "issues": sampling.issues,
            "random": sampling.random,
            "label": sampling.describe(),
            "seed": SAMPLE_SEED,
        },
        "counts": result.counts(),
        "issues": [
            {
                "sourceRow": row,
                "key": key,
                "code": issue.code,
                "message": issue.message,
                "severity": issue.severity.value,
            }
            for row, key, issue in result.issues
            if row in wanted
        ],
        "notes": result.notes,
    }
